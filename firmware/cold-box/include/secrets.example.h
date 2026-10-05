// Copy to secrets.h (git-ignored) and fill in. CI compiles with these placeholder values.
#pragma once

#define WIFI_SSID "your-ssid"
#define WIFI_PASS "your-password"
#define MQTT_HOST "192.168.43.100"  // LAN IP of the laptop running the broker (see README)
#define MQTT_PORT 1883
#define DEVICE_ID "cb-01"           // must match the Device registered in the hub
#define TEMP_OFFSET_C 0.0f          // added to every probe reading; calibrate in ice water (README)

// Optional battery gauge (battery -> voltage divider -> ADC1 pin, e.g. GPIO34).
// Uncomment all four to send "battery"; leave BATTERY_ADC_PIN undefined to omit the field.
// #define BATTERY_ADC_PIN 34
// #define BATTERY_DIVIDER 2.0f     // battery voltage / pin voltage
// #define BATTERY_EMPTY_MV 3300    // reported as 0 %
// #define BATTERY_FULL_MV 4200     // reported as 100 %
