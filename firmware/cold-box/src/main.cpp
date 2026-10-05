// Cold box: DS18B20 probe -> MQTT telemetry every 10 s.
// Only measures and reports; cold-chain rules (excursions, silence) are evaluated in the hub.
#include <Arduino.h>
#include <DallasTemperature.h>
#include <OneWire.h>
#include <PubSubClient.h>
#include <WiFi.h>
#include <time.h>

#if __has_include("secrets.h")
#include "secrets.h"
#else
#include "secrets.example.h"  // placeholders, so CI compiles without real credentials
#endif

constexpr uint8_t PROBE_PIN = 4;
constexpr uint32_t SAMPLE_MS = 10000;
constexpr size_t BUFFER_LEN = 60;  // 10 min of readings kept while offline
constexpr uint32_t BACKOFF_MIN_MS = 1000;
constexpr uint32_t BACKOFF_MAX_MS = 60000;
constexpr time_t CLOCK_VALID_AFTER = 1700000000;  // earlier means NTP has not synced yet

static const char TOPIC[] = "careE/devices/" DEVICE_ID "/telemetry";

struct Reading {
  time_t ts;
  float temp_c;
  int battery;  // -1: no battery gauge wired, field omitted
};

OneWire oneWire(PROBE_PIN);
DallasTemperature probe(&oneWire);
WiFiClient net;
PubSubClient mqtt(net);

Reading buffer[BUFFER_LEN];  // ring buffer, oldest reading at `head`
size_t head = 0;
size_t count = 0;
uint32_t lastSampleMs = 0;
uint32_t nextAttemptMs = 0;
uint32_t backoffMs = BACKOFF_MIN_MS;
bool ntpStarted = false;

int readBattery() {
#ifdef BATTERY_ADC_PIN
  // ponytail: linear voltage-to-percent map; Li-ion discharge is non-linear, use a lookup table if it matters.
  float mv = analogReadMilliVolts(BATTERY_ADC_PIN) * BATTERY_DIVIDER;
  int pct = (int)((mv - BATTERY_EMPTY_MV) * 100 / (BATTERY_FULL_MV - BATTERY_EMPTY_MV));
  return constrain(pct, 0, 100);
#else
  return -1;
#endif
}

void sample() {
  time_t now = time(nullptr);
  if (now < CLOCK_VALID_AFTER) {
    Serial.println("waiting for NTP, reading skipped");
    return;
  }
  probe.requestTemperatures();
  float raw = probe.getTempCByIndex(0);
  // -127 = probe not found; 85 = DS18B20 power-on value, not a real measurement.
  if (raw == DEVICE_DISCONNECTED_C || raw == 85.0f) {
    Serial.printf("probe error (%.1f), reading skipped\n", raw);
    return;
  }
  if (count == BUFFER_LEN) {  // full: drop the oldest
    head = (head + 1) % BUFFER_LEN;
    count--;
  }
  buffer[(head + count) % BUFFER_LEN] = {now, raw + TEMP_OFFSET_C, readBattery()};
  count++;
}

bool publish(const Reading& r) {
  char ts[21];
  char msg[128];
  struct tm utc;
  gmtime_r(&r.ts, &utc);
  strftime(ts, sizeof ts, "%Y-%m-%dT%H:%M:%SZ", &utc);
  int n = snprintf(msg, sizeof msg, "{\"device_id\":\"%s\",\"ts\":\"%s\",\"temp_c\":%.2f", DEVICE_ID,
                   ts, r.temp_c);
  if (r.battery >= 0) n += snprintf(msg + n, sizeof msg - n, ",\"battery\":%d", r.battery);
  snprintf(msg + n, sizeof msg - n, "}");
  bool ok = mqtt.publish(TOPIC, msg);
  if (ok) Serial.println(msg);
  return ok;
}

void flush() {
  while (count > 0 && mqtt.connected() && publish(buffer[head])) {
    head = (head + 1) % BUFFER_LEN;
    count--;
  }
}

bool connect() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("connecting Wi-Fi");
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    if (WiFi.waitForConnectResult(10000) != WL_CONNECTED) return false;
  }
  if (!ntpStarted) {  // the RTC keeps time afterwards, so offline readings stay timestamped
    configTime(0, 0, "pool.ntp.org", "time.google.com");
    ntpStarted = true;
  }
  Serial.println("connecting MQTT");
  return mqtt.connect(DEVICE_ID);
}

void keepConnected() {
  if (WiFi.status() == WL_CONNECTED && mqtt.connected()) return;
  if ((int32_t)(millis() - nextAttemptMs) < 0) return;
  if (connect()) {
    Serial.println("connected");
    backoffMs = BACKOFF_MIN_MS;
    return;
  }
  Serial.printf("connect failed, retry in %lu ms\n", (unsigned long)backoffMs);
  nextAttemptMs = millis() + backoffMs;
  backoffMs = min(backoffMs * 2, BACKOFF_MAX_MS);
}

void setup() {
  Serial.begin(115200);
  probe.begin();
  WiFi.mode(WIFI_STA);
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
}

void loop() {
  keepConnected();
  mqtt.loop();
  if (millis() - lastSampleMs >= SAMPLE_MS) {
    lastSampleMs = millis();
    sample();
  }
  flush();
}
