#include <Preferences.h>
#include <driver/gpio.h>

// RK3506B communicates with the ESP32-S3 through the CH341 UART bridge.
#define Serial Serial0

// Relay inputs are high-level triggered. N/P/K map to IN1/IN2/IN3.
// Flow meters are pulse-output sensors. Their signal wires must be 3.3 V
// safe (use an open-collector pull-up or level shifter for 5 V outputs).
constexpr int PUMP_COUNT = 3;
constexpr int PUMP_PINS[PUMP_COUNT] = {4, 5, 6};
constexpr int FLOW_PINS[PUMP_COUNT] = {15, 16, 17};
constexpr const char *PUMP_NAMES[PUMP_COUNT] = {"n", "p", "k"};
constexpr const char *FLOW_NAMES[PUMP_COUNT] = {"N_FLOW", "P_FLOW", "K_FLOW"};
constexpr int RAIN_PIN = 18;
constexpr int OUTLET_PUMP_PIN = 7;
constexpr float RAIN_MM_PER_TIP = 0.3F;
constexpr unsigned long RAIN_LOW_CONFIRM_MS = 30;
constexpr unsigned long RAIN_RELEASE_CONFIRM_MS = 100;
constexpr unsigned long RAIN_MIN_TIP_INTERVAL_MS = 800;
constexpr uint32_t FLOW_DEBOUNCE_US = 2000;
constexpr float DEFAULT_PULSES_PER_LITER = 450.0F;
constexpr unsigned long MAX_TEST_RUN_MS = 180000;

volatile uint32_t rainTips = 0;
volatile uint32_t rainRawEdges = 0;
volatile bool rainEdgePending = false;
volatile uint32_t flowPulses[PUMP_COUNT] = {0, 0, 0};
volatile uint32_t lastFlowPulseUs[PUMP_COUNT] = {0, 0, 0};
bool pumpOn[PUMP_COUNT] = {false, false, false};
unsigned long pumpStartedAt[PUMP_COUNT] = {0, 0, 0};
float pulsesPerLiter[PUMP_COUNT] = {DEFAULT_PULSES_PER_LITER, DEFAULT_PULSES_PER_LITER, DEFAULT_PULSES_PER_LITER};
uint32_t reportFlowPulses[PUMP_COUNT] = {0, 0, 0};
unsigned long reportFlowAt = 0;
unsigned long lastPeriodicReportAt = 0;
bool outletPumpOn = false;
bool fertigationActive = false;
float targetLiters[PUMP_COUNT] = {0, 0, 0};
uint32_t targetStartPulses[PUMP_COUNT] = {0, 0, 0};
unsigned long outletStartedAt = 0;
unsigned long outletTargetSeconds = 0;
String fertigationState = "idle";
String controlMode = "manual";
String lastError = "boot_safe_off";
String lastCommandId;
String serialLine;
Preferences preferences;

enum class RainInputState { ARMED, VERIFY_LOW, WAIT_RELEASE, VERIFY_RELEASE };
RainInputState rainInputState = RainInputState::WAIT_RELEASE;
unsigned long rainStateStartedAt = 0;
unsigned long lastAcceptedRainTipAt = 0;

void IRAM_ATTR onRainTip() {
  ++rainRawEdges;
  rainEdgePending = true;
}

void updateRainInput() {
  const unsigned long now = millis();
  bool pending = false;
  noInterrupts();
  pending = rainEdgePending;
  if (rainInputState != RainInputState::WAIT_RELEASE) rainEdgePending = false;
  interrupts();

  if (rainInputState == RainInputState::ARMED && pending) {
    rainInputState = RainInputState::VERIFY_LOW;
    rainStateStartedAt = now;
  }
  if (rainInputState == RainInputState::VERIFY_LOW) {
    if (digitalRead(RAIN_PIN) != LOW) {
      rainInputState = RainInputState::ARMED;
    } else if (now - rainStateStartedAt >= RAIN_LOW_CONFIRM_MS) {
      if (rainTips == 0 || now - lastAcceptedRainTipAt >= RAIN_MIN_TIP_INTERVAL_MS) {
        ++rainTips;
        lastAcceptedRainTipAt = now;
      }
      rainInputState = RainInputState::WAIT_RELEASE;
      noInterrupts();
      rainEdgePending = false;
      interrupts();
    }
  } else if (rainInputState == RainInputState::WAIT_RELEASE) {
    if (digitalRead(RAIN_PIN) == HIGH) {
      rainInputState = RainInputState::VERIFY_RELEASE;
      rainStateStartedAt = now;
    }
  } else if (rainInputState == RainInputState::VERIFY_RELEASE) {
    if (digitalRead(RAIN_PIN) == LOW) {
      rainInputState = RainInputState::WAIT_RELEASE;
    } else if (now - rainStateStartedAt >= RAIN_RELEASE_CONFIRM_MS) {
      rainInputState = RainInputState::ARMED;
      noInterrupts();
      rainEdgePending = false;
      interrupts();
    }
  }
}

void IRAM_ATTR onFlowN() {
  const uint32_t now = micros();
  if (now - lastFlowPulseUs[0] >= FLOW_DEBOUNCE_US) {
    ++flowPulses[0];
    lastFlowPulseUs[0] = now;
  }
}

void IRAM_ATTR onFlowP() {
  const uint32_t now = micros();
  if (now - lastFlowPulseUs[1] >= FLOW_DEBOUNCE_US) {
    ++flowPulses[1];
    lastFlowPulseUs[1] = now;
  }
}

void IRAM_ATTR onFlowK() {
  const uint32_t now = micros();
  if (now - lastFlowPulseUs[2] >= FLOW_DEBOUNCE_US) {
    ++flowPulses[2];
    lastFlowPulseUs[2] = now;
  }
}

uint32_t rainTipSnapshot() {
  noInterrupts();
  const uint32_t value = rainTips;
  interrupts();
  return value;
}

uint32_t rainRawEdgeSnapshot() {
  noInterrupts();
  const uint32_t value = rainRawEdges;
  interrupts();
  return value;
}

void flowPulseSnapshot(uint32_t *output) {
  noInterrupts();
  for (int index = 0; index < PUMP_COUNT; ++index) output[index] = flowPulses[index];
  interrupts();
}

String jsonEscape(const String &value) {
  String escaped;
  for (size_t index = 0; index < value.length(); ++index) {
    const char character = value[index];
    if (character == '\\' || character == '"') escaped += '\\';
    escaped += character;
  }
  return escaped;
}

String jsonString(const String &json, const char *key, const String &fallback = "") {
  const String marker = String("\"") + key + "\":\"";
  const int markerAt = json.indexOf(marker);
  if (markerAt < 0) return fallback;
  const int start = markerAt + marker.length();
  const int end = json.indexOf('"', start);
  return end > start ? json.substring(start, end) : fallback;
}

int pumpIndex(const String &name) {
  for (int index = 0; index < PUMP_COUNT; ++index) {
    if (name.equalsIgnoreCase(PUMP_NAMES[index])) return index;
  }
  return -1;
}

float jsonNumber(const String &json, const char *key, float fallback) {
  const String marker = String("\"") + key + "\":";
  const int markerAt = json.indexOf(marker);
  if (markerAt < 0) return fallback;
  int start = markerAt + marker.length();
  while (start < static_cast<int>(json.length()) && (json[start] == ' ' || json[start] == '\t')) ++start;
  int end = start;
  while (end < static_cast<int>(json.length()) && String("0123456789+-.eE").indexOf(json[end]) >= 0) ++end;
  return end > start ? json.substring(start, end).toFloat() : fallback;
}

bool anyPumpOn() {
  for (int index = 0; index < PUMP_COUNT; ++index) {
    if (pumpOn[index]) return true;
  }
  return false;
}

void setPump(int index, bool enabled, const char *reason) {
  if (index < 0 || index >= PUMP_COUNT) return;
  digitalWrite(PUMP_PINS[index], enabled ? HIGH : LOW);
  pumpOn[index] = enabled;
  pumpStartedAt[index] = enabled ? millis() : 0;
  if (enabled) {
    lastError = "";
  } else if (reason && reason[0]) {
    lastError = reason;
  }
}

void stopAll(const char *reason) {
  for (int index = 0; index < PUMP_COUNT; ++index) setPump(index, false, reason);
  digitalWrite(OUTLET_PUMP_PIN, LOW);
  outletPumpOn = false;
  outletStartedAt = 0;
  outletTargetSeconds = 0;
  fertigationActive = false;
  fertigationState = "idle";
  for (int index = 0; index < PUMP_COUNT; ++index) targetLiters[index] = 0;
}

void startOutletPump() {
  if (outletTargetSeconds == 0) {
    fertigationActive = false;
    fertigationState = "complete";
    return;
  }
  digitalWrite(OUTLET_PUMP_PIN, HIGH);
  outletPumpOn = true;
  outletStartedAt = millis();
  fertigationActive = true;
  fertigationState = "outlet";
  lastError = "";
}

unsigned long runSeconds(int index) {
  return pumpOn[index] ? (millis() - pumpStartedAt[index]) / 1000UL : 0;
}

float deliveredLiters(int index, uint32_t pulses[ PUMP_COUNT ]) {
  if (index < 0 || index >= PUMP_COUNT || pulsesPerLiter[index] <= 0) return 0.0F;
  return pulses[index] / pulsesPerLiter[index];
}

float flowRateLMin(int index, uint32_t pulses[ PUMP_COUNT ], unsigned long now) {
  if (index < 0 || index >= PUMP_COUNT || reportFlowAt == 0 || now <= reportFlowAt || pulsesPerLiter[index] <= 0) return 0.0F;
  const float elapsedMinutes = (now - reportFlowAt) / 60000.0F;
  return (pulses[index] - reportFlowPulses[index]) / pulsesPerLiter[index] / elapsedMinutes;
}

void updateFertigation() {
  if (!fertigationActive) return;
  if (fertigationState == "outlet") {
    if ((millis() - outletStartedAt) / 1000UL >= outletTargetSeconds) {
      digitalWrite(OUTLET_PUMP_PIN, LOW);
      outletPumpOn = false;
      outletStartedAt = 0;
      fertigationActive = false;
      fertigationState = "complete";
      lastError = "target_reached";
      reportSerialState();
    }
    return;
  }
  uint32_t pulses[PUMP_COUNT];
  flowPulseSnapshot(pulses);
  bool anyTarget = false;
  for (int index = 0; index < PUMP_COUNT; ++index) {
    if (targetLiters[index] <= 0) {
      setPump(index, false, "");
      continue;
    }
    anyTarget = true;
    const float delivered = (pulses[index] - targetStartPulses[index]) / pulsesPerLiter[index];
    if (delivered >= targetLiters[index]) setPump(index, false, "target_reached");
  }
  bool anyOn = anyPumpOn();
  if (!anyTarget || !anyOn) {
    startOutletPump();
  } else {
    fertigationState = "dosing";
  }
}

void saveMode() {
  preferences.begin("zhirun-valve", false);
  preferences.putString("mode", "manual");
  preferences.end();
}

void reportSerialState() {
  const uint32_t tips = rainTipSnapshot();
  const uint32_t rawRainEdges = rainRawEdgeSnapshot();
  uint32_t pulses[PUMP_COUNT];
  flowPulseSnapshot(pulses);
  const unsigned long now = millis();
  const float nFlow = flowRateLMin(0, pulses, now);
  const float pFlow = flowRateLMin(1, pulses, now);
  const float kFlow = flowRateLMin(2, pulses, now);
  Serial.println(String("STATE {\"controllerSchema\":\"four_relay_independent_flow_v1\",\"firmwareVersion\":\"four_relay_job_v3_rain_filter\",\"valveOn\":") +
      (anyPumpOn() ? "true" : "false") +
      ",\"manualOpen\":" + (anyPumpOn() ? "true" : "false") +
      ",\"nPumpOn\":" + (pumpOn[0] ? "true" : "false") +
      ",\"pPumpOn\":" + (pumpOn[1] ? "true" : "false") +
      ",\"kPumpOn\":" + (pumpOn[2] ? "true" : "false") +
      ",\"gpioHigh\":" + (digitalRead(PUMP_PINS[0]) == HIGH ? "true" : "false") +
      ",\"gpioLevel\":" + String(digitalRead(PUMP_PINS[0])) +
      ",\"gpio4High\":" + (digitalRead(PUMP_PINS[0]) == HIGH ? "true" : "false") +
      ",\"gpio4Level\":" + String(digitalRead(PUMP_PINS[0])) +
      ",\"gpio5High\":" + (digitalRead(PUMP_PINS[1]) == HIGH ? "true" : "false") +
      ",\"gpio5Level\":" + String(digitalRead(PUMP_PINS[1])) +
      ",\"gpio6High\":" + (digitalRead(PUMP_PINS[2]) == HIGH ? "true" : "false") +
      ",\"gpio6Level\":" + String(digitalRead(PUMP_PINS[2])) +
      // Legacy fields continue to reflect IN1 while older displays migrate.
      ",\"gpio42High\":" + (digitalRead(PUMP_PINS[0]) == HIGH ? "true" : "false") +
      ",\"gpio42Level\":" + String(digitalRead(PUMP_PINS[0])) +
      ",\"activeHigh\":true,\"mode\":\"manual\"" +
      ",\"runSeconds\":" + String(runSeconds(0)) +
      ",\"nRunSeconds\":" + String(runSeconds(0)) +
      ",\"pRunSeconds\":" + String(runSeconds(1)) +
      ",\"kRunSeconds\":" + String(runSeconds(2)) +
      ",\"maxRunS\":" + String(MAX_TEST_RUN_MS / 1000UL) +
      ",\"outletPumpOn\":" + (outletPumpOn ? "true" : "false") +
      ",\"outletRunSeconds\":" + String(outletPumpOn ? (now - outletStartedAt) / 1000UL : 0) +
      ",\"outletTargetSeconds\":" + String(outletTargetSeconds) +
      ",\"fertigationState\":\"" + fertigationState + "\"" +
      ",\"jobActive\":" + (fertigationActive ? "true" : "false") +
      ",\"nFlowPulses\":" + String(pulses[0]) +
      ",\"pFlowPulses\":" + String(pulses[1]) +
      ",\"kFlowPulses\":" + String(pulses[2]) +
      ",\"nDeliveredL\":" + String(deliveredLiters(0, pulses), 3) +
      ",\"pDeliveredL\":" + String(deliveredLiters(1, pulses), 3) +
      ",\"kDeliveredL\":" + String(deliveredLiters(2, pulses), 3) +
      ",\"nFlowLMin\":" + String(nFlow, 3) +
      ",\"pFlowLMin\":" + String(pFlow, 3) +
      ",\"kFlowLMin\":" + String(kFlow, 3) +
      ",\"nPulsesPerL\":" + String(pulsesPerLiter[0], 3) +
      ",\"pPulsesPerL\":" + String(pulsesPerLiter[1], 3) +
      ",\"kPulsesPerL\":" + String(pulsesPerLiter[2], 3) +
      ",\"flowMeters\":{\"N_FLOW\":true,\"P_FLOW\":true,\"K_FLOW\":true}" +
      ",\"rainTips\":" + String(tips) +
      ",\"rainMm\":" + String(tips * RAIN_MM_PER_TIP, 1) +
      ",\"rainPinLevel\":" + String(digitalRead(RAIN_PIN)) +
      ",\"rainRawEdges\":" + String(rawRainEdges) +
      ",\"rainFilteredEdges\":" + String(rawRainEdges >= tips ? rawRainEdges - tips : 0) +
      ",\"error\":\"" + jsonEscape(lastError) +
      "\",\"lastCommandId\":\"" + jsonEscape(lastCommandId) + "\"}");
  for (int index = 0; index < PUMP_COUNT; ++index) reportFlowPulses[index] = pulses[index];
  reportFlowAt = now;
}

void handleCommand(const String &json) {
  const int commandAt = json.indexOf("\"command\":{");
  if (commandAt < 0) return;
  const String command = json.substring(commandAt);
  lastCommandId = jsonString(command, "id", lastCommandId);
  const String action = jsonString(command, "action");

  if (action == "pump_test") {
    const int index = pumpIndex(jsonString(command, "pump"));
    const String requested = jsonString(command, "manual_action");
    if (index < 0) {
      lastError = "bad_pump";
    } else if (requested == "open" || requested == "on") {
      setPump(index, true, "");
    } else if (requested == "close" || requested == "off") {
      setPump(index, false, "manual_close");
    } else {
      lastError = "bad_manual_action";
    }
  } else if (action == "fertigation_start") {
    uint32_t pulses[PUMP_COUNT];
    flowPulseSnapshot(pulses);
    targetLiters[0] = max(0.0F, jsonNumber(command, "n_target_l", 0));
    targetLiters[1] = max(0.0F, jsonNumber(command, "p_target_l", 0));
    targetLiters[2] = max(0.0F, jsonNumber(command, "k_target_l", 0));
    outletTargetSeconds = static_cast<unsigned long>(max(0.0F, jsonNumber(command, "outlet_run_s", 0)));
    outletStartedAt = 0;
    digitalWrite(OUTLET_PUMP_PIN, LOW);
    outletPumpOn = false;
    for (int index = 0; index < PUMP_COUNT; ++index) {
      targetStartPulses[index] = pulses[index];
      setPump(index, targetLiters[index] > 0, "");
    }
    fertigationActive = anyPumpOn();
    if (fertigationActive) fertigationState = "dosing";
    else startOutletPump();
  } else if (action == "fertigation_stop") {
    // The dashboard and the HMI both stop a job through this action. Without
    // its own branch it fell through to unsupported_command and left the
    // relays running, so treat it as a full stop.
    stopAll("fertigation_stop");
  } else if (action == "outlet_test") {
    const String requested = jsonString(command, "manual_action");
    if (requested == "open" || requested == "on") {
      // The mixing-tank outlet is tested independently, never together with
      // an N/P/K dosing pump. Limit a manual test to the normal safety cap.
      for (int index = 0; index < PUMP_COUNT; ++index) setPump(index, false, "outlet_test");
      outletTargetSeconds = min(180UL, static_cast<unsigned long>(max(1.0F, jsonNumber(command, "run_seconds", 10))));
      startOutletPump();
    } else if (requested == "close" || requested == "off") {
      stopAll("outlet_test_stop");
    } else {
      lastError = "bad_manual_action";
    }
  } else if (action == "manual") {
    const String requested = jsonString(command, "manual_action");
    if (requested == "close" || requested == "off") {
      stopAll("manual_close");
    } else if (requested == "open" || requested == "on") {
      // Backward compatibility: the former single-pump start controls IN1/N.
      setPump(0, true, "");
    } else {
      lastError = "bad_manual_action";
    }
  } else if (action == "mode" || action == "config") {
    stopAll(action == "mode" ? "mode_changed" : "config_updated");
    controlMode = "manual";
    saveMode();
    if (action == "config") {
      pulsesPerLiter[0] = max(1.0F, jsonNumber(command, "n_pulses_per_l", pulsesPerLiter[0]));
      pulsesPerLiter[1] = max(1.0F, jsonNumber(command, "p_pulses_per_l", pulsesPerLiter[1]));
      pulsesPerLiter[2] = max(1.0F, jsonNumber(command, "k_pulses_per_l", pulsesPerLiter[2]));
    }
  } else if (action == "sensor") {
    // Sensor frames are accepted for protocol compatibility but never start a pump.
  } else {
    lastError = "unsupported_command";
  }
  reportSerialState();
}

void pollSerial() {
  while (Serial.available()) {
    const char character = static_cast<char>(Serial.read());
    if (character == '\r') continue;
    if (character != '\n') {
      if (serialLine.length() < 900) serialLine += character;
      else serialLine = "";
      continue;
    }
    if (serialLine == "STATUS") reportSerialState();
    else if (serialLine.indexOf("\"command\":{") >= 0) handleCommand(serialLine);
    serialLine = "";
  }
}

void setup() {
  Serial.begin(115200);
  delay(200);
  for (int index = 0; index < PUMP_COUNT; ++index) {
    pinMode(PUMP_PINS[index], OUTPUT);
    gpio_set_drive_capability(static_cast<gpio_num_t>(PUMP_PINS[index]), GPIO_DRIVE_CAP_3);
    digitalWrite(PUMP_PINS[index], LOW);
    pinMode(FLOW_PINS[index], INPUT_PULLUP);
  }
  pinMode(OUTLET_PUMP_PIN, OUTPUT);
  digitalWrite(OUTLET_PUMP_PIN, LOW);
  pinMode(RAIN_PIN, INPUT_PULLUP);
  // A dry, normally-open tipping bucket must idle HIGH. Do not arm the
  // counter while the contact is already closed or the signal is shorted.
  rainInputState = digitalRead(RAIN_PIN) == HIGH
      ? RainInputState::ARMED : RainInputState::WAIT_RELEASE;
  rainStateStartedAt = millis();
  attachInterrupt(digitalPinToInterrupt(RAIN_PIN), onRainTip, FALLING);
  attachInterrupt(digitalPinToInterrupt(FLOW_PINS[0]), onFlowN, FALLING);
  attachInterrupt(digitalPinToInterrupt(FLOW_PINS[1]), onFlowP, FALLING);
  attachInterrupt(digitalPinToInterrupt(FLOW_PINS[2]), onFlowK, FALLING);
  stopAll("boot_safe_off");
  saveMode();
  reportSerialState();
}

void loop() {
  updateRainInput();
  pollSerial();
  updateFertigation();
  // A start command records pumpStartedAt inside pollSerial(). Read the
  // current time afterwards so unsigned subtraction cannot wrap and trigger
  // an immediate false max-runtime shutdown.
  const unsigned long now = millis();
  for (int index = 0; index < PUMP_COUNT; ++index) {
    if (pumpOn[index] && now - pumpStartedAt[index] >= MAX_TEST_RUN_MS) {
      setPump(index, false, "max_runtime_reached");
      reportSerialState();
    }
  }
  if (fertigationActive && now - lastPeriodicReportAt >= 1000) {
    reportSerialState();
    lastPeriodicReportAt = now;
  }
  delay(20);
}
