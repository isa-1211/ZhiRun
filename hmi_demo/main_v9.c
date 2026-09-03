#include <lvgl/lvgl.h>
#include <arpa/inet.h>
#include <ctype.h>
#include <netdb.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

extern void lv_port_init(int width, int height, int rotation);

#ifndef HMI_SERVER_HOST
#define HMI_SERVER_HOST "8.145.49.45"
#endif
#ifndef HMI_SERVER_PORT
#define HMI_SERVER_PORT 80
#endif

static lv_obj_t *status_label;
static lv_obj_t *metric_labels[13];
static lv_obj_t *source_label;
static lv_obj_t *pump_label;
static lv_obj_t *pages[5];
static lv_obj_t *weather_label;
static lv_obj_t *model_label;
static lv_obj_t *network_label;
static lv_obj_t *valve_detail_label;
static unsigned current_page;

#define BOOT_FRAME_FILE "/userdata/zhirun/zhirun_boot_frames.rgb565"
#define BOOT_AUDIO_FILE "/userdata/zhirun/zhirun_boot_audio.wav"
#define BOOT_FRAME_WIDTH 800
#define BOOT_FRAME_HEIGHT 480
#define BOOT_FRAME_BYTES (BOOT_FRAME_WIDTH * BOOT_FRAME_HEIGHT * 2)
#define BOOT_FRAME_COUNT 18
#define BOOT_FRAME_INTERVAL_MS 333
#define BOOT_DURATION_MS 6000

static uint8_t *boot_frame_data;
static lv_image_dsc_t boot_frame_dsc;
static FILE *boot_frame_file;
static uint32_t boot_frame_index;
static uint32_t boot_start_tick;

static void start_boot_audio(void) {
    pid_t child = fork();
    if (child != 0) return;
    execl("/usr/bin/aplay", "aplay", "-q", "-D", "hw:0,0",
          BOOT_AUDIO_FILE, (char *)NULL);
    _exit(127);
}

static int request(const char *method, const char *path, const char *body,
                   char *out, size_t cap) {
    char port[16], message[1024];
    struct addrinfo hints = {0}, *address = NULL;
    snprintf(port, sizeof(port), "%d", HMI_SERVER_PORT);
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    if (getaddrinfo(HMI_SERVER_HOST, port, &hints, &address) != 0) return -1;

    int fd = socket(address->ai_family, address->ai_socktype, address->ai_protocol);
    if (fd < 0) {
        freeaddrinfo(address);
        return -1;
    }
    struct timeval timeout = {.tv_sec = 2, .tv_usec = 0};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    if (connect(fd, address->ai_addr, address->ai_addrlen) != 0) {
        close(fd);
        freeaddrinfo(address);
        return -1;
    }
    freeaddrinfo(address);

    int length = snprintf(
        message, sizeof(message),
        "%s %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n"
        "Content-Type: application/json\r\nContent-Length: %zu\r\n\r\n%s",
        method, path, HMI_SERVER_HOST, body ? strlen(body) : 0, body ? body : "");
    if (length < 0 || (size_t)length >= sizeof(message) ||
        send(fd, message, (size_t)length, 0) < 0) {
        close(fd);
        return -1;
    }

    size_t used = 0;
    while (used + 1 < cap) {
        ssize_t received = recv(fd, out + used, cap - used - 1, 0);
        if (received <= 0) break;
        used += (size_t)received;
    }
    out[used] = 0;
    close(fd);
    if (strstr(out, " 200 ") == NULL && strstr(out, " 202 ") == NULL) return -1;
    char *body_start = strstr(out, "\r\n\r\n");
    if (!body_start) return -1;
    memmove(out, body_start + 4, strlen(body_start + 4) + 1);
    return 0;
}

static const char *json_value(const char *json, const char *key) {
    char pattern[80];
    snprintf(pattern, sizeof(pattern), "\"%s\":", key);
    const char *value = strstr(json, pattern);
    if (!value) return NULL;
    value += strlen(pattern);
    while (isspace((unsigned char)*value)) value++;
    return value;
}

static bool json_number(const char *json, const char *key, double *number) {
    const char *value = json_value(json, key);
    if (!value || strncmp(value, "null", 4) == 0) return false;
    char *end = NULL;
    *number = strtod(value, &end);
    return end != value;
}

static bool json_boolean(const char *json, const char *key, bool *result) {
    const char *value = json_value(json, key);
    if (!value) return false;
    if (strncmp(value, "true", 4) == 0) {
        *result = true;
        return true;
    }
    if (strncmp(value, "false", 5) == 0) {
        *result = false;
        return true;
    }
    return false;
}

static bool json_string(const char *json, const char *key, char *out, size_t cap) {
    const char *value = json_value(json, key);
    if (!value || *value != '"') return false;
    value++;
    const char *end = strchr(value, '"');
    if (!end) return false;
    size_t length = (size_t)(end - value);
    if (length >= cap) length = cap - 1;
    memcpy(out, value, length);
    out[length] = 0;
    return true;
}

static void set_metric(unsigned index, bool available, double value,
                       unsigned precision, const char *unit) {
    char text[48];
    if (available) {
        snprintf(text, sizeof(text), precision == 0 ? "%.0f %s" :
                 precision == 2 ? "%.2f %s" : "%.1f %s", value, unit);
        lv_label_set_text(metric_labels[index], text);
    } else {
        lv_label_set_text(metric_labels[index], "--");
    }
}

static void refresh(lv_timer_t *timer) {
    (void)timer;
    fprintf(stderr, "HMI_REFRESH begin\n");
    char response[4096], device[64] = "unknown", source[32] = "unknown";
    double values[13] = {0}, age;

    if (request("GET", "/data", NULL, response, sizeof(response)) != 0) {
        fprintf(stderr, "HMI_REFRESH data_request_failed\n");
        lv_label_set_text(status_label, "Device offline");
        for (unsigned index = 0; index < 13; index++) lv_label_set_text(metric_labels[index], "--");
        lv_label_set_text(source_label, "Check Ethernet or Wi-Fi");
        return;
    }
    fprintf(stderr, "HMI_REFRESH data_received\n");

    static const char *keys[] = {
        "airTemp", "airHum", "co2", "lux", "soilMoist", "soilTemp",
        "soilPH", "soilEc", "n", "p", "k", "windSpeed", "rainMm"
    };
    static const char *units[] = {
        "C", "%", "ppm", "lux", "%", "C", "", "dS/m",
        "mg/kg", "mg/kg", "mg/kg", "m/s", "mm"
    };
    static const unsigned precision[] = {1, 1, 0, 0, 1, 1, 2, 2, 0, 0, 0, 1, 1};
    bool available[13];
    for (unsigned index = 0; index < 13; index++) {
        available[index] = json_number(response, keys[index], &values[index]);
        set_metric(index, available[index], values[index], precision[index], units[index]);
    }
    bool has_age = json_number(response, "_age", &age);
    json_string(response, "_device_id", device, sizeof(device));
    json_string(response, "_source", source, sizeof(source));
    fprintf(stderr, "HMI_REFRESH metrics_updated\n");

    bool live = strcmp(source, "rk3506") == 0 && (!has_age || age < 30.0);
    lv_label_set_text(status_label, live ? "Device online" : "Data stale");
    char source_text[180];
    if (has_age)
        snprintf(source_text, sizeof(source_text), "Device: %s | Source: %s | Age: %.0f s", device, source, age);
    else
        snprintf(source_text, sizeof(source_text), "Device: %s | Source: %s", device, source);
    lv_label_set_text(source_label, source_text);
    fprintf(stderr, "HMI_REFRESH source_updated\n");

    char weather_text[220];
    snprintf(weather_text, sizeof(weather_text),
             "Air temp %.1f C\nAir humidity %.1f %%\nWind %.1f m/s\nRain %.1f mm",
             values[0], values[1], values[11], values[12]);
    if (weather_label) lv_label_set_text(weather_label, weather_text);
    if (model_label) lv_label_set_text(model_label,
        "Model: server\nExtraTrees multi-output policy\nDaily 12:00 automatic run; manual work order available\nMissing fertilizer data allows water-only irrigation; invalid soil data blocks safely");

    if (request("GET", "/valve/config", NULL, response, sizeof(response)) == 0) {
        bool online = false, pump_on = false, n_on = false, p_on = false, k_on = false, outlet_on = false;
        json_boolean(response, "online", &online);
        if (!online) {
            lv_label_set_text(pump_label, "Controller offline");
            if (valve_detail_label) lv_label_set_text(valve_detail_label, "Valve status unavailable");
        }
        else {
            json_boolean(response, "valveOn", &pump_on);
            json_boolean(response, "nPumpOn", &n_on);
            json_boolean(response, "pPumpOn", &p_on);
            json_boolean(response, "kPumpOn", &k_on);
            json_boolean(response, "outletPumpOn", &outlet_on);
            lv_label_set_text(pump_label, pump_on ? "Irrigation: ON" : "Irrigation: OFF");
            char detail[240];
            snprintf(detail, sizeof(detail), "N pump GPIO4: %s\nP pump GPIO5: %s\nK pump GPIO6: %s\nOutlet GPIO7: %s",
                     n_on ? "ON" : "OFF", p_on ? "ON" : "OFF",
                     k_on ? "ON" : "OFF", outlet_on ? "ON" : "OFF");
            if (valve_detail_label) lv_label_set_text(valve_detail_label, detail);
        }
    }
    if (network_label) {
        char network_text[160];
        snprintf(network_text, sizeof(network_text), "Server: http://%s:%d\nWi-Fi or Ethernet supported\nUSB insertion order is independent",
                 HMI_SERVER_HOST, HMI_SERVER_PORT);
        lv_label_set_text(network_label, network_text);
    }
    fprintf(stderr, "HMI_REFRESH complete\n");
}

static void stop_pump(lv_event_t *event) {
    (void)event;
    char response[1024];
    if (request("POST", "/valve/manual", "{\"action\":\"close\"}", response, sizeof(response)) == 0)
        lv_label_set_text(pump_label, "STOP queued");
    else
        lv_label_set_text(pump_label, "Command failed");
}

static lv_obj_t *make_panel(lv_obj_t *parent, int x, int y, int width, int height) {
    lv_obj_t *panel = lv_obj_create(parent);
    lv_obj_set_pos(panel, x, y);
    lv_obj_set_size(panel, width, height);
    lv_obj_set_style_radius(panel, 6, 0);
    lv_obj_set_style_bg_color(panel, lv_color_hex(0x141D29), 0);
    lv_obj_set_style_border_color(panel, lv_color_hex(0x2A3A4F), 0);
    lv_obj_set_style_pad_all(panel, 9, 0);
    lv_obj_clear_flag(panel, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(panel, LV_OBJ_FLAG_GESTURE_BUBBLE);
    return panel;
}

static void gesture_page(lv_event_t *event);

static lv_obj_t *make_page(lv_obj_t *parent) {
    lv_obj_t *page = lv_obj_create(parent);
    lv_obj_set_pos(page, 10, 108);
    lv_obj_set_size(page, 780, 325);
    lv_obj_set_style_bg_color(page, lv_color_hex(0x101925), 0);
    lv_obj_set_style_border_color(page, lv_color_hex(0x26364C), 0);
    lv_obj_set_style_pad_all(page, 12, 0);
    lv_obj_clear_flag(page, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(page, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_add_event_cb(page, gesture_page, LV_EVENT_GESTURE, NULL);
    return page;
}

static lv_obj_t *page_text(lv_obj_t *page, const char *text, int x, int y, int width) {
    lv_obj_t *label = lv_label_create(page);
    lv_label_set_text(label, text);
    lv_obj_set_width(label, width);
    lv_obj_set_pos(label, x, y);
    lv_obj_set_style_text_color(label, lv_color_hex(0xDCE6F4), 0);
    lv_obj_set_style_text_line_space(label, 7, 0);
    lv_obj_add_flag(label, LV_OBJ_FLAG_GESTURE_BUBBLE);
    return label;
}

static void show_page(unsigned selected) {
    if (selected >= 5) return;
    current_page = selected;
    for (unsigned i = 0; i < 5; i++) {
        if (!pages[i]) continue;
        if (i == selected) lv_obj_clear_flag(pages[i], LV_OBJ_FLAG_HIDDEN);
        else lv_obj_add_flag(pages[i], LV_OBJ_FLAG_HIDDEN);
    }
}

static void switch_page(lv_event_t *event) {
    show_page((unsigned)(uintptr_t)lv_event_get_user_data(event));
}

static void gesture_page(lv_event_t *event) {
    (void)event;
    lv_indev_t *indev = lv_indev_active();
    if (!indev) return;
    lv_dir_t direction = lv_indev_get_gesture_dir(indev);
    if (direction == LV_DIR_LEFT) show_page((current_page + 1) % 5);
    else if (direction == LV_DIR_RIGHT) show_page((current_page + 4) % 5);
}

static void build_dashboard(void) {
    lv_obj_t *screen = lv_scr_act();
    lv_obj_set_style_bg_color(screen, lv_color_hex(0x0B1017), 0);
    lv_obj_add_event_cb(screen, gesture_page, LV_EVENT_GESTURE, NULL);

    lv_obj_t *title = lv_label_create(screen);
    lv_label_set_text(title, "ZhiRun");
    lv_obj_set_style_text_color(title, lv_color_hex(0xE8EDF5), 0);
    lv_obj_set_pos(title, 18, 7);

    lv_obj_t *subtitle = lv_label_create(screen);
    lv_label_set_text(subtitle, "Smart Agriculture Fertigation System");
    lv_obj_set_style_text_color(subtitle, lv_color_hex(0x8FA3C0), 0);
    lv_obj_set_pos(subtitle, 18, 34);

    status_label = lv_label_create(screen);
    lv_label_set_text(status_label, "Starting");
    lv_obj_set_style_text_color(status_label, lv_color_hex(0x58D3AE), 0);
    lv_obj_align(status_label, LV_ALIGN_TOP_RIGHT, -18, 12);

    static const char *tabs[] = {"Data", "Weather", "Valves", "Model", "Network"};
    for (unsigned i = 0; i < 5; i++) {
        lv_obj_t *tab = lv_btn_create(screen);
        lv_obj_set_pos(tab, 10 + (int)i * 156, 67);
        lv_obj_set_size(tab, 148, 33);
        lv_obj_add_event_cb(tab, switch_page, LV_EVENT_CLICKED, (void *)(uintptr_t)i);
        lv_obj_t *label = lv_label_create(tab);
        lv_label_set_text(label, tabs[i]);
        lv_obj_center(label);
    }

    for (unsigned i = 0; i < 5; i++) pages[i] = make_page(screen);
    for (unsigned i = 1; i < 5; i++) lv_obj_add_flag(pages[i], LV_OBJ_FLAG_HIDDEN);

    static const char *names[] = {
        "Air temperature", "Air humidity", "CO2", "Light", "Soil moisture",
        "Soil temperature", "Soil pH", "Soil EC", "Nitrogen N", "Phosphorus P", "Potassium K", "Wind", "Rain"
    };
    for (unsigned index = 0; index < 13; index++) {
        int column = (int)(index % 4);
        int row = (int)(index / 4);
        lv_obj_t *panel = make_panel(pages[0], 7 + column * 188, 7 + row * 92, 178, 82);
        lv_obj_t *name = lv_label_create(panel);
        lv_label_set_text(name, names[index]);
        lv_obj_set_style_text_color(name, lv_color_hex(0x91A3BA), 0);
        metric_labels[index] = lv_label_create(panel);
        lv_label_set_text(metric_labels[index], "--");
        lv_obj_set_style_text_color(metric_labels[index], lv_color_hex(0xF1F5FA), 0);
        lv_obj_align(metric_labels[index], LV_ALIGN_BOTTOM_LEFT, 0, -2);
    }

    lv_obj_t *control_panel = make_panel(pages[2], 7, 7, 230, 91);
    lv_obj_t *control_title = lv_label_create(control_panel);
    lv_label_set_text(control_title, "Pump status");
    lv_obj_set_style_text_color(control_title, lv_color_hex(0x58D3AE), 0);
    pump_label = lv_label_create(control_panel);
    lv_label_set_text(pump_label, "State unknown");
    lv_obj_set_width(pump_label, 210);
    lv_obj_set_pos(pump_label, 0, 27);

    lv_obj_t *action_panel = make_panel(pages[2], 252, 7, 230, 91);
    lv_obj_t *button = lv_btn_create(action_panel);
    lv_obj_set_size(button, 210, 58);
    lv_obj_align(button, LV_ALIGN_BOTTOM_MID, 0, 0);
    lv_obj_add_event_cb(button, stop_pump, LV_EVENT_CLICKED, NULL);
    lv_obj_t *button_label = lv_label_create(button);
    lv_label_set_text(button_label, "STOP ALL");
    lv_obj_center(button_label);

    valve_detail_label = page_text(pages[2], "Waiting for valve status", 7, 119, 740);
    weather_label = page_text(pages[1], "Waiting for environment data", 7, 7, 740);
    model_label = page_text(pages[3], "Model: server\nExtraTrees multi-output policy\nDaily 12:00 automatic run", 7, 7, 740);
    network_label = page_text(pages[4], "Waiting for network status", 7, 7, 740);

    source_label = lv_label_create(screen);
    lv_label_set_text(source_label, "Waiting for server data");
    lv_obj_set_width(source_label, 760);
    lv_obj_set_style_text_color(source_label, lv_color_hex(0x8293A8), 0);
    lv_obj_set_pos(source_label, 18, 445);

    lv_timer_create(refresh, 5000, NULL);
}

static void finish_boot(lv_timer_t *timer) {
    lv_obj_t *boot_image = (lv_obj_t *)timer->user_data;
    lv_timer_del(timer);
    if (boot_image) lv_obj_del(boot_image);
    if (boot_frame_file) fclose(boot_frame_file);
    boot_frame_file = NULL;
    free(boot_frame_data);
    boot_frame_data = NULL;
    build_dashboard();
}

static bool load_boot_frames(void) {
    boot_frame_file = fopen(BOOT_FRAME_FILE, "rb");
    if (!boot_frame_file) return false;
    if (fseek(boot_frame_file, 0, SEEK_END) != 0) {
        fclose(boot_frame_file);
        boot_frame_file = NULL;
        return false;
    }
    long length = ftell(boot_frame_file);
    if (length != (long)(BOOT_FRAME_BYTES * BOOT_FRAME_COUNT)) {
        fclose(boot_frame_file);
        boot_frame_file = NULL;
        return false;
    }
    rewind(boot_frame_file);
    boot_frame_data = malloc(BOOT_FRAME_BYTES);
    if (!boot_frame_data ||
        fread(boot_frame_data, 1, BOOT_FRAME_BYTES, boot_frame_file) != BOOT_FRAME_BYTES) {
        free(boot_frame_data);
        boot_frame_data = NULL;
        fclose(boot_frame_file);
        boot_frame_file = NULL;
        return false;
    }
    memset(&boot_frame_dsc, 0, sizeof(boot_frame_dsc));
    boot_frame_dsc.header.magic = LV_IMAGE_HEADER_MAGIC;
    boot_frame_dsc.header.cf = LV_COLOR_FORMAT_RGB565;
    boot_frame_dsc.header.w = BOOT_FRAME_WIDTH;
    boot_frame_dsc.header.h = BOOT_FRAME_HEIGHT;
    boot_frame_dsc.header.stride = BOOT_FRAME_WIDTH * 2;
    boot_frame_dsc.data_size = BOOT_FRAME_BYTES;
    boot_frame_dsc.data = boot_frame_data;
    return true;
}

static void advance_boot_frame(lv_timer_t *timer) {
    lv_obj_t *boot_image = (lv_obj_t *)timer->user_data;
    uint32_t elapsed = lv_tick_elaps(boot_start_tick);
    if (elapsed >= BOOT_DURATION_MS) {
        finish_boot(timer);
        return;
    }
    if (!boot_frame_data) return;
    uint32_t next_index = elapsed / BOOT_FRAME_INTERVAL_MS;
    if (next_index >= BOOT_FRAME_COUNT) next_index = BOOT_FRAME_COUNT - 1;
    if (next_index == boot_frame_index) return;
    if (fseek(boot_frame_file, (long)(next_index * BOOT_FRAME_BYTES), SEEK_SET) != 0 ||
        fread(boot_frame_data, 1, BOOT_FRAME_BYTES, boot_frame_file) != BOOT_FRAME_BYTES)
        return;
    boot_frame_index = next_index;
    lv_image_cache_drop(&boot_frame_dsc);
    lv_image_set_src(boot_image, &boot_frame_dsc);
    lv_obj_invalidate(boot_image);
}

static void show_boot_screen(void) {
    lv_obj_t *screen = lv_scr_act();
    lv_obj_set_style_bg_color(screen, lv_color_hex(0x000000), 0);
    lv_obj_t *boot_image = lv_image_create(screen);
    bool loaded = load_boot_frames();
    fprintf(stderr, "HMI_BOOT frames_loaded=%d bytes=%u\n", loaded ? 1 : 0,
            loaded ? (unsigned)(BOOT_FRAME_BYTES * BOOT_FRAME_COUNT) : 0);
    if (loaded) {
        boot_frame_index = 0;
        boot_frame_dsc.data = boot_frame_data;
        lv_image_set_src(boot_image, &boot_frame_dsc);
    }
    lv_obj_set_size(boot_image, BOOT_FRAME_WIDTH, BOOT_FRAME_HEIGHT);
    lv_obj_center(boot_image);
    lv_obj_update_layout(screen);
    fprintf(stderr, "HMI_BOOT object_size=%dx%d\n", (int)lv_obj_get_width(boot_image),
            (int)lv_obj_get_height(boot_image));
    /* Commit the first frame before audio starts, then use one shared epoch. */
    lv_refr_now(NULL);
    boot_start_tick = lv_tick_get();
    start_boot_audio();
    lv_timer_create(advance_boot_frame, 50, boot_image);
}

int main(void) {
    lv_port_init(0, 0, 0);
    show_boot_screen();
    while (1) {
        lv_timer_handler();
        usleep(5000);
    }
    return 0;
}
