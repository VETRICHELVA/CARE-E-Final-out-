# cold-box firmware

An ESP32 reads a DS18B20 temperature probe every 10 s and publishes it over MQTT to `careE/devices/{device_id}/telemetry`, where `services/iot-ingest` picks it up:

```json
{ "device_id": "cb-01", "ts": "2026-11-20T10:15:00Z", "temp_c": 4.31, "battery": 82 }
```

The firmware only measures and reports. Cold-chain rules (excursions, silence) are evaluated in the hub.

- Time comes from NTP. No readings are recorded until the clock has synced.
- While Wi-Fi or the broker is unreachable, up to 60 readings (10 min) are buffered and published, oldest first, on reconnect. If the buffer fills, the oldest reading is dropped.
- Wi-Fi and MQTT reconnect with exponential backoff, from 1 s up to 60 s.
- `battery` is sent only if a battery gauge is configured (`BATTERY_ADC_PIN`). A USB power bank cannot be measured, so the field is normally omitted.
- Probe errors (-127 = not found, 85 = power-on value) are skipped, not published. A failed probe therefore shows in the hub as silence: after 2 minutes without a reading on an IN_TRANSIT shipment the hub raises DEVICE_SILENT, never an excursion (business-rules.md §11). The cold-chain rules run only in the hub.

No hardware? `scripts/simulate_telemetry.py` publishes the same messages (see the end of this file).

## Hardware

ESP32 DevKit (esp32dev), DS18B20 waterproof probe, 4.7 kΩ resistor, USB power bank, insulated box with gel packs.

| DS18B20 wire      | ESP32                        |
| ----------------- | ---------------------------- |
| Red (VCC)         | 3.3 V                        |
| Black (GND)       | GND                          |
| Yellow/white (DQ) | GPIO4                        |
| 4.7 kΩ pull-up    | between DQ (GPIO4) and 3.3 V |

Wire colours vary between probe vendors; check the probe's datasheet.

## Configure and flash

1. Install PlatformIO: `uv tool install platformio`.
2. `cp include/secrets.example.h include/secrets.h` and fill in Wi-Fi, `MQTT_HOST` and `DEVICE_ID`. `secrets.h` is git-ignored. Without it the build uses the example placeholders (that is how CI compiles).
3. Connect the ESP32 by USB, then from `firmware/cold-box`:

   ```sh
   pio run                    # compile
   pio run -t upload          # flash
   pio device monitor         # serial log at 115200 baud
   ```

   On Linux, if the upload cannot open the port, add yourself to the `dialout` group and log in again.

### Reaching the broker

`infra/docker-compose.yml` binds Mosquitto to `127.0.0.1` only, and it allows anonymous clients, so the ESP32 cannot reach it as is. For a hardware test, forward a LAN port on the laptop for the duration of the test only, on a network you control:

```sh
socat TCP-LISTEN:1884,fork,reuseaddr TCP:127.0.0.1:1883
```

Then set `MQTT_HOST` to the laptop's IP on that network and `MQTT_PORT` to `1884`. Stop `socat` when you are done.

Tip: use a phone hotspot. Join both the laptop and the ESP32 to it (the ESP32 needs 2.4 GHz). The laptop's IP is shown by `ip -4 addr`, usually `192.168.43.x` on Android or `172.20.10.x` on iPhone. This avoids campus or office Wi-Fi that blocks device-to-device traffic or needs a captive-portal login.

## Manual hardware test

Record the result (date, device ID, offset, pass or fail per step) in `docs/build/PROGRESS.md` under S14.

1. **Boot.** Flash, open the serial monitor. Expect `connecting Wi-Fi`, `connecting MQTT`, `connected`, then one JSON line every 10 s. Readings taken before NTP syncs log `waiting for NTP, reading skipped`.
2. **Broker.** From the repo root on the laptop:

   ```sh
   docker compose -f infra/docker-compose.yml exec -T mosquitto \
     mosquitto_sub -t 'careE/devices/+/telemetry' -v
   ```

   Expect a message every 10 s with the right `device_id` and a `ts` within a few seconds of the current UTC time.

3. **Calibrate.** Put the probe and a reference thermometer in stirred ice water for 2 minutes. Set `TEMP_OFFSET_C` to (reference − reported), reflash, and confirm the reported value is within 0.2 °C of the reference.
4. **Cold box.** Put the probe in the box with gel packs. Readings should settle within 2–8 °C. Hold the probe in your hand: readings should rise above 8 °C within a minute.
5. **Offline buffer.** Turn the hotspot off for about 3 minutes, then back on. Within about a minute of reconnecting, the subscriber should receive about 18 readings in a burst, with the original 10 s-apart timestamps and no gap.
6. **Probe unplugged.** Disconnect the probe's data wire. The serial log shows `probe error (-127.0), reading skipped` and nothing is published. With the box on an IN_TRANSIT shipment, the hub raises DEVICE_SILENT within about 2.5 minutes (not an excursion). Reconnect it and publishing resumes.

## Simulator

```sh
uv run scripts/simulate_telemetry.py --device cb-01 --profile normal|excursion|silent [--interval 10]
```

- `normal`: 3.5–5.5 °C, forever.
- `excursion`: normal for 1 minute, then 9.1 and 9.4 °C, then normal again. On a 2–8 °C shipment the hub raises EXCURSION on 9.4 °C and RECOVERED two normal readings later (Scenario 2).
- `silent`: normal for 1 minute, then stops publishing. On an IN_TRANSIT shipment the hub's worker raises DEVICE_SILENT once 2 minutes pass without a reading (checked every 30 s).

`--host` and `--port` default to `localhost:1883` (the `make up` broker).
