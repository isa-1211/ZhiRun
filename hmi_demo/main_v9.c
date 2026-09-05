#include <lvgl/lvgl.h>
#include <lvgl/src/widgets/keyboard/lv_keyboard.h>
#include <lvgl/src/widgets/textarea/lv_textarea.h>
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
static lv_obj_t *metric_labels[12];
static lv_obj_t *source_label;
static lv_obj_t *pump_label;
static lv_obj_t *pages[5];
static lv_obj_t *weather_label;
static lv_obj_t *model_label;
static lv_obj_t *network_label;
static lv_obj_t *wifi_scan_label;
static lv_obj_t *wifi_network_list;
static lv_obj_t *wifi_ssid_input;
static lv_obj_t *wifi_password_input;
static lv_obj_t *wifi_keyboard;
static bool wifi_scan_active;
static bool wifi_connect_active;
static uint32_t wifi_connect_start_tick;
static char wifi_scan_ssids[12][160];
static lv_obj_t *valve_detail_label;
/* N / P / K dosing pumps and the mixing-tank outlet pump, in that order. */
static lv_obj_t *pump_state_labels[4];
static lv_obj_t *valve_action_label;
static unsigned current_page;

#define BOOT_FRAME_FILE "/userdata/zhirun/zhirun_boot_frames.rgb565"
#define BOOT_AUDIO_FILE "/userdata/zhirun/zhirun_boot_audio.wav"
#define BOOT_FRAME_WIDTH 800
#define BOOT_FRAME_HEIGHT 480
#define BOOT_FRAME_BYTES (BOOT_FRAME_WIDTH * BOOT_FRAME_HEIGHT * 2)
#define BOOT_FRAME_COUNT 18
#define BOOT_FRAME_INTERVAL_MS 333
#define BOOT_DURATION_MS 6000

#define WIFI_SCAN_FILE "/tmp/zhirun_hmi_wifi_scan.txt"
#define WIFI_SCAN_STATUS_FILE "/tmp/zhirun_hmi_wifi_scan.status"
#define WIFI_CONNECT_STATUS_FILE "/tmp/zhirun_hmi_wifi_connect.status"

static uint8_t *boot_frame_data;
static lv_image_dsc_t boot_frame_dsc;
static FILE *boot_frame_file;
static uint32_t boot_frame_index;
static uint32_t boot_start_tick;

static void write_status_file(const char *path, const char *text) {
    FILE *file = fopen(path, "w");
    if (!file) return;
    fputs(text, file);
    fputc('\n', file);
    fclose(file);
}

static void shell_quote(const char *input, char *output, size_t cap) {
    size_t used = 0;
    if (cap == 0) return;
    output[used++] = '\'';
    for (const char *cursor = input; *cursor && used + 5 < cap; cursor++) {
        if (*cursor == '\'') {
            memcpy(output + used, "'\\''", 4);
            used += 4;
        } else {
            output[used++] = *cursor;
        }
    }
    if (used + 1 < cap) output[used++] = '\'';
    output[used] = 0;
}

static void wifi_scan_start(lv_event_t *event) {
    (void)event;
    if (wifi_scan_active) return;
    unlink(WIFI_SCAN_FILE);
    unlink(WIFI_SCAN_STATUS_FILE);
    pid_t child = fork();
    if (child < 0) {
        lv_label_set_text(wifi_scan_label, "Wi-Fi scan could not start");
        return;
    }
    if (child == 0) {
        int result = system("wpa_cli -i wlan0 scan >/dev/null 2>&1; sleep 3; "
                            "wpa_cli -i wlan0 scan_results > " WIFI_SCAN_FILE " 2>/dev/null");
        write_status_file(WIFI_SCAN_STATUS_FILE, result == 0 ? "done" : "failed");
        _exit(result == 0 ? 0 : 1);
    }
    wifi_scan_active = true;
    lv_label_set_text(wifi_scan_label, "Scanning nearby Wi-Fi...");
}

static void wifi_network_selected(lv_event_t *event) {
    lv_obj_t *button = lv_event_get_target(event);
    const char *ssid = (const char *)lv_event_get_user_data(event);
    if (!ssid || !*ssid || !wifi_ssid_input) return;
    lv_textarea_set_text(wifi_ssid_input, ssid);
    lv_textarea_set_text(wifi_password_input, "");
    if (wifi_keyboard) {
        lv_keyboard_set_textarea(wifi_keyboard, wifi_password_input);
        lv_obj_clear_flag(wifi_keyboard, LV_OBJ_FLAG_HIDDEN);
    }
    lv_obj_add_state(button, LV_STATE_CHECKED);
    lv_label_set_text(wifi_scan_label, "Selected network; enter password, then CONNECT");
}

static void wifi_network_list_clear(void) {
    if (wifi_network_list) lv_obj_clean(wifi_network_list);
    memset(wifi_scan_ssids, 0, sizeof(wifi_scan_ssids));
}

static void wifi_network_add(unsigned index, const char *ssid, int rssi, bool secured) {
    if (!wifi_network_list || index >= 12 || !ssid || !*ssid) return;
    snprintf(wifi_scan_ssids[index], sizeof(wifi_scan_ssids[index]), "%s", ssid);
    lv_obj_t *button = lv_btn_create(wifi_network_list);
    lv_obj_set_pos(button, 3, 3 + (int)index * 34);
    lv_obj_set_size(button, 748, 30);
    lv_obj_add_flag(button, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_add_event_cb(button, wifi_network_selected, LV_EVENT_CLICKED,
                        wifi_scan_ssids[index]);
    lv_obj_t *name = lv_label_create(button);
    char label[220];
    snprintf(label, sizeof(label), "%s%s", secured ? "[LOCK] " : "[OPEN] ", ssid);
    lv_label_set_text(name, label);
    lv_obj_align(name, LV_ALIGN_LEFT_MID, 8, 0);
    lv_obj_t *signal = lv_label_create(button);
    char signal_text[32];
    snprintf(signal_text, sizeof(signal_text), "%d dBm", rssi);
    lv_label_set_text(signal, signal_text);
    lv_obj_align(signal, LV_ALIGN_RIGHT_MID, -8, 0);
}

static void wifi_scan_poll(void) {
    if (!wifi_scan_active || access(WIFI_SCAN_STATUS_FILE, F_OK) != 0) return;
    FILE *status = fopen(WIFI_SCAN_STATUS_FILE, "r");
    char state[16] = "failed";
    if (status) {
        fgets(state, sizeof(state), status);
        fclose(status);
    }
    wifi_scan_active = false;
    if (strncmp(state, "done", 4) != 0) {
        lv_label_set_text(wifi_scan_label, "Wi-Fi scan failed");
        return;
    }
    FILE *results = fopen(WIFI_SCAN_FILE, "r");
    if (!results) {
        lv_label_set_text(wifi_scan_label, "No Wi-Fi networks found");
        return;
    }
    wifi_network_list_clear();
    char line[320];
    unsigned count = 0;
    while (fgets(line, sizeof(line), results) && count < 12) {
        if (strncmp(line, "bssid /", 7) == 0 || strncmp(line, "Selected", 8) == 0) continue;
        char *columns[5] = {0};
        char *cursor = line;
        for (unsigned column = 0; column < 5; column++) {
            columns[column] = cursor;
            char *tab = strchr(cursor, '\t');
            if (!tab) break;
            *tab = 0;
            cursor = tab + 1;
        }
        if (!columns[4]) continue;
        char *ssid = columns[4];
        ssid[strcspn(ssid, "\r\n")] = 0;
        if (!*ssid) continue;
        int rssi = atoi(columns[2] ? columns[2] : "0");
        bool secured = columns[3] && strchr(columns[3], 'W') != NULL;
        wifi_network_add(count++, ssid, rssi, secured);
    }
    fclose(results);
    if (count == 0) {
        lv_label_set_text(wifi_scan_label, "No Wi-Fi networks found");
    } else {
        lv_label_set_text(wifi_scan_label, "Tap a network, enter its password, then CONNECT");
    }
}

static int run_wpa_value(const char *id, const char *key, const char *value) {
    char quoted[320], command[520], value_with_quotes[260];
    snprintf(value_with_quotes, sizeof(value_with_quotes), "\"%s\"", value);
    shell_quote(value_with_quotes, quoted, sizeof(quoted));
    snprintf(command, sizeof(command), "wpa_cli -i wlan0 set_network %s %s %s >/dev/null 2>&1",
             id, key, quoted);
    return system(command);
}

static void wifi_connect_start(lv_event_t *event) {
    (void)event;
    if (wifi_connect_active) return;
    const char *ssid = lv_textarea_get_text(wifi_ssid_input);
    const char *password = lv_textarea_get_text(wifi_password_input);
    if (!ssid || !*ssid) {
        lv_label_set_text(wifi_scan_label, "Enter an SSID first");
        return;
    }
    char ssid_copy[160], password_copy[160];
    snprintf(ssid_copy, sizeof(ssid_copy), "%s", ssid);
    snprintf(password_copy, sizeof(password_copy), "%s", password ? password : "");
    unlink(WIFI_CONNECT_STATUS_FILE);
    pid_t child = fork();
    if (child < 0) {
        lv_label_set_text(wifi_scan_label, "Wi-Fi connection could not start");
        return;
    }
    if (child == 0) {
        char quoted_ssid[360], command[520], id[32] = "";
        char ssid_value[190];
        snprintf(ssid_value, sizeof(ssid_value), "\"%s\"", ssid_copy);
        shell_quote(ssid_value, quoted_ssid, sizeof(quoted_ssid));
        FILE *networks = popen("wpa_cli -i wlan0 add_network 2>/dev/null", "r");
        if (networks) {
            fgets(id, sizeof(id), networks);
            pclose(networks);
        }
        id[strcspn(id, "\r\n")] = 0;
        if (!*id || !isdigit((unsigned char)id[0]) ||
            run_wpa_value(id, "ssid", ssid_copy) != 0) {
            write_status_file(WIFI_CONNECT_STATUS_FILE, "failed");
            _exit(1);
        }
        if (*password_copy) {
            if (run_wpa_value(id, "psk", password_copy) != 0) {
                write_status_file(WIFI_CONNECT_STATUS_FILE, "failed");
                _exit(1);
            }
        } else {
            snprintf(command, sizeof(command),
                     "wpa_cli -i wlan0 set_network %s key_mgmt NONE >/dev/null 2>&1", id);
            if (system(command) != 0) {
                write_status_file(WIFI_CONNECT_STATUS_FILE, "failed");
                _exit(1);
            }
        }
        snprintf(command, sizeof(command),
                 "wpa_cli -i wlan0 set_network %s scan_ssid 1 >/dev/null 2>&1; "
                 "wpa_cli -i wlan0 enable_network %s >/dev/null 2>&1; "
                 "wpa_cli -i wlan0 select_network %s >/dev/null 2>&1; "
                 "wpa_cli -i wlan0 save_config >/dev/null 2>&1; "
                 "ip link set wlan0 up >/dev/null 2>&1; dhcpcd wlan0 >/dev/null 2>&1",
                 id, id, id);
        int result = system(command);
        write_status_file(WIFI_CONNECT_STATUS_FILE, result == 0 ? "connecting" : "failed");
        _exit(result == 0 ? 0 : 1);
    }
    wifi_connect_active = true;
    wifi_connect_start_tick = lv_tick_get();
    lv_label_set_text(wifi_scan_label, "Wi-Fi configuration sent; waiting for association...");
}

static void wifi_input_event(lv_event_t *event) {
    if (!wifi_keyboard) return;
    lv_event_code_t code = lv_event_get_code(event);
    if (code == LV_EVENT_FOCUSED) {
        lv_keyboard_set_textarea(wifi_keyboard, lv_event_get_target(event));
        lv_obj_clear_flag(wifi_keyboard, LV_OBJ_FLAG_HIDDEN);
    } else if (code == LV_EVENT_READY || code == LV_EVENT_CANCEL) {
        lv_obj_add_flag(wifi_keyboard, LV_OBJ_FLAG_HIDDEN);
    }
}

static void wifi_local_status(void) {
    FILE *pipe = popen("wpa_cli -i wlan0 status 2>/dev/null", "r");
    char line[220], ssid[160] = "", state[32] = "";
    if (pipe) {
        while (fgets(line, sizeof(line), pipe)) {
            if (strncmp(line, "ssid=", 5) == 0) snprintf(ssid, sizeof(ssid), "%s", line + 5);
            else if (strncmp(line, "wpa_state=", 10) == 0) snprintf(state, sizeof(state), "%s", line + 10);
        }
        pclose(pipe);
    }
    ssid[strcspn(ssid, "\r\n")] = 0;
    state[strcspn(state, "\r\n")] = 0;
    char text[320];
    snprintf(text, sizeof(text), "Local Wi-Fi: %s\nState: %s\nOffline setup works without the server",
             *ssid ? ssid : "not connected", *state ? state : "unavailable");
    if (network_label) lv_label_set_text(network_label, text);
    if (wifi_connect_active) {
        bool child_failed = false;
        FILE *connect_status = fopen(WIFI_CONNECT_STATUS_FILE, "r");
        if (connect_status) {
            char connect_state[24] = "";
            fgets(connect_state, sizeof(connect_state), connect_status);
            fclose(connect_status);
            child_failed = strncmp(connect_state, "failed", 6) == 0;
        }
        if (strcmp(state, "COMPLETED") == 0) {
            wifi_connect_active = false;
            lv_label_set_text(wifi_scan_label, "Wi-Fi connected successfully");
        } else if (child_failed || lv_tick_elaps(wifi_connect_start_tick) > 60000) {
            wifi_connect_active = false;
            lv_label_set_text(wifi_scan_label, "Wi-Fi connection failed; check password");
        }
    }
    wifi_scan_poll();
}

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
    double values[12] = {0}, age;

    wifi_local_status();

    if (request("GET", "/data", NULL, response, sizeof(response)) != 0) {
        fprintf(stderr, "HMI_REFRESH data_request_failed\n");
        lv_label_set_text(status_label, "Device offline");
        for (unsigned index = 0; index < 12; index++) lv_label_set_text(metric_labels[index], "--");
        lv_label_set_text(source_label, "Check Ethernet or Wi-Fi");
        return;
    }
    fprintf(stderr, "HMI_REFRESH data_received\n");

    static const char *keys[] = {
        "airTemp", "airHum", "co2", "lux", "soilMoist", "soilTemp",
        "soilPH", "n", "p", "k", "windSpeed", "rainMm"
    };
    static const char *units[] = {
        "C", "%", "ppm", "lux", "%", "C", "",
        "mg/kg", "mg/kg", "mg/kg", "m/s", "mm"
    };
    static const unsigned precision[] = {1, 1, 0, 0, 1, 1, 2, 0, 0, 0, 1, 1};
    bool available[12];
    for (unsigned index = 0; index < 12; index++) {
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
             values[0], values[1], values[10], values[11]);
    if (weather_label) lv_label_set_text(weather_label, weather_text);
    if (model_label) lv_label_set_text(model_label,
        "Model: server\nExtraTrees multi-output policy\nDaily 12:00 automatic run; manual work order available\nMissing fertilizer data allows water-only irrigation; invalid soil data blocks safely");

    if (request("GET", "/valve/config", NULL, response, sizeof(response)) == 0) {
        static const char *state_keys[] = {"nPumpOn", "pPumpOn", "kPumpOn", "outletPumpOn"};
        bool online = false, pump_on = false, states[4] = {false, false, false, false};
        json_boolean(response, "online", &online);
        if (!online) {
            lv_label_set_text(pump_label, "Controller offline");
            for (unsigned index = 0; index < 4; index++)
                if (pump_state_labels[index]) lv_label_set_text(pump_state_labels[index], "--");
            if (valve_detail_label) lv_label_set_text(valve_detail_label, "Valve status unavailable");
        }
        else {
            json_boolean(response, "valveOn", &pump_on);
            for (unsigned index = 0; index < 4; index++) {
                json_boolean(response, state_keys[index], &states[index]);
                if (pump_state_labels[index])
                    lv_label_set_text(pump_state_labels[index], states[index] ? "ON" : "OFF");
            }
            lv_label_set_text(pump_label, pump_on ? "Irrigation: ON" : "Irrigation: OFF");
            char stage[32] = "idle", detail[240];
            double delivered[3] = {0, 0, 0}, outlet_seconds = 0;
            json_string(response, "fertigationState", stage, sizeof(stage));
            json_number(response, "nDeliveredL", &delivered[0]);
            json_number(response, "pDeliveredL", &delivered[1]);
            json_number(response, "kDeliveredL", &delivered[2]);
            json_number(response, "outletRunSeconds", &outlet_seconds);
            snprintf(detail, sizeof(detail),
                     "Stage: %s\nDelivered  N %.2f L   P %.2f L   K %.2f L\nOutlet running %.0f s",
                     stage, delivered[0], delivered[1], delivered[2], outlet_seconds);
            if (valve_detail_label) lv_label_set_text(valve_detail_label, detail);
        }
    }
    if (network_label) {
        char network_text[320];
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

/* The N/P/K dosing pumps share the bounded test endpoint; the mixing-tank
 * outlet pump has its own endpoint because it carries a run duration. */
static void pump_command(unsigned index, bool start) {
    static const char *pump_keys[] = {"n", "p", "k"};
    char body[96], response[1024];
    const char *path;
    if (index < 3) {
        path = "/pump/test";
        snprintf(body, sizeof(body), "{\"pump\":\"%s\",\"action\":\"%s\"}",
                 pump_keys[index], start ? "open" : "close");
    } else {
        path = "/outlet/test";
        snprintf(body, sizeof(body), "{\"action\":\"%s\",\"run_seconds\":10}",
                 start ? "open" : "close");
    }
    const int sent = request("POST", path, body, response, sizeof(response));
    if (valve_action_label)
        lv_label_set_text(valve_action_label,
                          sent == 0 ? (start ? "Start queued" : "Stop queued")
                                    : "Command failed");
}

static void pump_button(lv_event_t *event) {
    const uintptr_t data = (uintptr_t)lv_event_get_user_data(event);
    pump_command((unsigned)(data >> 1), (data & 1u) != 0);
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
    /* The data page contains 12 cards. Keep the viewport compact and let the
     * user scroll vertically through all cards on the 800x480 display. */
    lv_obj_add_flag(pages[0], LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scroll_dir(pages[0], LV_DIR_VER);
    for (unsigned i = 1; i < 5; i++) lv_obj_add_flag(pages[i], LV_OBJ_FLAG_HIDDEN);

    static const char *names[] = {
        "Air temperature", "Air humidity", "CO2", "Light", "Soil moisture",
        "Soil temperature", "Soil pH", "Nitrogen N", "Phosphorus P", "Potassium K", "Wind", "Rain"
    };
    for (unsigned index = 0; index < 12; index++) {
        int column = (int)(index % 3);
        int row = (int)(index / 3);
        lv_obj_t *panel = make_panel(pages[0], 7 + column * 252, 7 + row * 90, 244, 82);
        lv_obj_t *name = lv_label_create(panel);
        lv_label_set_text(name, names[index]);
        lv_obj_set_style_text_color(name, lv_color_hex(0x91A3BA), 0);
        metric_labels[index] = lv_label_create(panel);
        lv_label_set_text(metric_labels[index], "--");
        lv_obj_set_style_text_color(metric_labels[index], lv_color_hex(0xF1F5FA), 0);
        lv_obj_align(metric_labels[index], LV_ALIGN_BOTTOM_LEFT, 0, -2);
    }

    /* The valve page holds a status header, one control row per pump and a
     * job detail line, so let it scroll vertically like the data page. */
    lv_obj_add_flag(pages[2], LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scroll_dir(pages[2], LV_DIR_VER);
    /* Network setup is touch-first: the scan result area is an independent
     * scrollable list so all nearby SSIDs remain selectable on the 800x480
     * display while the on-screen keyboard is open. */
    lv_obj_add_flag(pages[4], LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scroll_dir(pages[4], LV_DIR_VER);

    lv_obj_t *control_panel = make_panel(pages[2], 7, 7, 360, 66);
    lv_obj_t *control_title = lv_label_create(control_panel);
    lv_label_set_text(control_title, "Pump status");
    lv_obj_set_style_text_color(control_title, lv_color_hex(0x58D3AE), 0);
    pump_label = lv_label_create(control_panel);
    lv_label_set_text(pump_label, "State unknown");
    lv_obj_set_width(pump_label, 335);
    lv_obj_set_pos(pump_label, 0, 25);

    lv_obj_t *action_panel = make_panel(pages[2], 380, 7, 360, 66);
    lv_obj_t *button = lv_btn_create(action_panel);
    lv_obj_set_size(button, 338, 44);
    lv_obj_center(button);
    lv_obj_add_flag(button, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_add_event_cb(button, stop_pump, LV_EVENT_CLICKED, NULL);
    lv_obj_t *button_label = lv_label_create(button);
    lv_label_set_text(button_label, "STOP ALL");
    lv_obj_center(button_label);

    static const char *pump_rows[] = {
        "N pump   IN1 / GPIO4", "P pump   IN2 / GPIO5",
        "K pump   IN3 / GPIO6", "Outlet pump   IN4 / GPIO7"
    };
    for (unsigned index = 0; index < 4; index++) {
        lv_obj_t *row = make_panel(pages[2], 7, 81 + (int)index * 56, 733, 50);
        lv_obj_t *name = lv_label_create(row);
        lv_label_set_text(name, pump_rows[index]);
        lv_obj_set_style_text_color(name, lv_color_hex(0xDCE6F4), 0);
        lv_obj_align(name, LV_ALIGN_LEFT_MID, 0, 0);

        pump_state_labels[index] = lv_label_create(row);
        lv_label_set_text(pump_state_labels[index], "--");
        lv_obj_set_style_text_color(pump_state_labels[index], lv_color_hex(0x58D3AE), 0);
        lv_obj_align(pump_state_labels[index], LV_ALIGN_LEFT_MID, 260, 0);

        /* user_data packs the pump index and the requested action: bit 0 set
         * means start, clear means stop. */
        lv_obj_t *start = lv_btn_create(row);
        lv_obj_set_size(start, 108, 30);
        lv_obj_align(start, LV_ALIGN_RIGHT_MID, -118, 0);
        lv_obj_add_flag(start, LV_OBJ_FLAG_GESTURE_BUBBLE);
        lv_obj_add_event_cb(start, pump_button, LV_EVENT_CLICKED,
                            (void *)(uintptr_t)((index << 1) | 1u));
        lv_obj_t *start_label = lv_label_create(start);
        lv_label_set_text(start_label, "START");
        lv_obj_center(start_label);

        lv_obj_t *stop = lv_btn_create(row);
        lv_obj_set_size(stop, 108, 30);
        lv_obj_align(stop, LV_ALIGN_RIGHT_MID, 0, 0);
        lv_obj_add_flag(stop, LV_OBJ_FLAG_GESTURE_BUBBLE);
        lv_obj_add_event_cb(stop, pump_button, LV_EVENT_CLICKED,
                            (void *)(uintptr_t)(index << 1));
        lv_obj_t *stop_label = lv_label_create(stop);
        lv_label_set_text(stop_label, "STOP");
        lv_obj_center(stop_label);
    }

    valve_action_label = page_text(pages[2], "Ready", 7, 311, 733);
    valve_detail_label = page_text(pages[2], "Waiting for valve status", 7, 337, 733);
    weather_label = page_text(pages[1], "Waiting for environment data", 7, 7, 740);
    model_label = page_text(pages[3], "Model: server\nExtraTrees multi-output policy\nDaily 12:00 automatic run", 7, 7, 740);
    network_label = page_text(pages[4], "Local Wi-Fi: checking...", 7, 7, 740);

    wifi_ssid_input = lv_textarea_create(pages[4]);
    lv_obj_set_pos(wifi_ssid_input, 7, 72);
    lv_obj_set_size(wifi_ssid_input, 260, 42);
    lv_textarea_set_one_line(wifi_ssid_input, true);
    lv_textarea_set_placeholder_text(wifi_ssid_input, "SSID");
    lv_obj_add_event_cb(wifi_ssid_input, wifi_input_event, LV_EVENT_FOCUSED, NULL);
    lv_obj_add_event_cb(wifi_ssid_input, wifi_input_event, LV_EVENT_READY, NULL);
    lv_obj_add_event_cb(wifi_ssid_input, wifi_input_event, LV_EVENT_CANCEL, NULL);

    wifi_password_input = lv_textarea_create(pages[4]);
    lv_obj_set_pos(wifi_password_input, 280, 72);
    lv_obj_set_size(wifi_password_input, 260, 42);
    lv_textarea_set_one_line(wifi_password_input, true);
    lv_textarea_set_password_mode(wifi_password_input, true);
    lv_textarea_set_placeholder_text(wifi_password_input, "Password (empty for open Wi-Fi)");
    lv_obj_add_event_cb(wifi_password_input, wifi_input_event, LV_EVENT_FOCUSED, NULL);
    lv_obj_add_event_cb(wifi_password_input, wifi_input_event, LV_EVENT_READY, NULL);
    lv_obj_add_event_cb(wifi_password_input, wifi_input_event, LV_EVENT_CANCEL, NULL);

    lv_obj_t *scan_button = lv_btn_create(pages[4]);
    lv_obj_set_pos(scan_button, 555, 72);
    lv_obj_set_size(scan_button, 105, 42);
    lv_obj_add_event_cb(scan_button, wifi_scan_start, LV_EVENT_CLICKED, NULL);
    lv_obj_t *scan_button_label = lv_label_create(scan_button);
    lv_label_set_text(scan_button_label, "SCAN");
    lv_obj_center(scan_button_label);

    lv_obj_t *connect_button = lv_btn_create(pages[4]);
    lv_obj_set_pos(connect_button, 668, 72);
    lv_obj_set_size(connect_button, 105, 42);
    lv_obj_add_event_cb(connect_button, wifi_connect_start, LV_EVENT_CLICKED, NULL);
    lv_obj_t *connect_button_label = lv_label_create(connect_button);
    lv_label_set_text(connect_button_label, "CONNECT");
    lv_obj_center(connect_button_label);

    wifi_scan_label = page_text(pages[4], "Tap SCAN to list nearby networks", 7, 132, 760);
    wifi_network_list = lv_obj_create(pages[4]);
    lv_obj_set_pos(wifi_network_list, 7, 168);
    lv_obj_set_size(wifi_network_list, 760, 135);
    lv_obj_set_style_bg_color(wifi_network_list, lv_color_hex(0x0B1017), 0);
    lv_obj_set_style_border_color(wifi_network_list, lv_color_hex(0x26364C), 0);
    lv_obj_set_style_pad_all(wifi_network_list, 0, 0);
    lv_obj_add_flag(wifi_network_list, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scroll_dir(wifi_network_list, LV_DIR_VER);
    wifi_keyboard = lv_keyboard_create(screen);
    lv_obj_set_size(wifi_keyboard, 800, 180);
    lv_obj_set_pos(wifi_keyboard, 0, 300);
    lv_obj_add_flag(wifi_keyboard, LV_OBJ_FLAG_HIDDEN);

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
