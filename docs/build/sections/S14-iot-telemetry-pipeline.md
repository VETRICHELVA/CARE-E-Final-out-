# S14 — IoT telemetry pipeline

**Milestone:** M3 · **Depends on:** S02 (and S11 for linking readings to shipments) · **Workstream:** IoT · **Can run alongside:** almost anything; firmware and simulator can start right after S01

## Read first
- `docs/specs/apps-ai-iot.md` — IoT (Hardware, Firmware, Ingest, Simulator)
- `docs/specs/domain-model.md` — IoT (Device, SensorReading)
- `docs/specs/api-and-events.md` — S14 rows; `coldchain.reading` event

## Goal
Temperature readings from the ESP32 cold box (or the simulator) reach the hub within seconds, de-duplicated, and are attached to the shipment the device is carrying.

## Build
- **`firmware/cold-box`** (PlatformIO, Arduino framework):
  - Reads the DS18B20 (OneWire + DallasTemperature libraries); gets time from NTP.
  - Publishes over MQTT (PubSubClient) every 10 s to `careE/devices/{id}/telemetry`.
  - Keeps an offline buffer of 60 readings and reconnects with backoff.
  - `secrets.example.h` is committed; `secrets.h` is git-ignored.
  - README with the wiring: data → GPIO4, 4.7 kΩ pull-up to 3.3 V.
- **`services/iot-ingest`:**
  - paho-mqtt subscriber to `careE/devices/+/telemetry`, validating with pydantic and de-duplicating in memory on (device_id, ts).
  - Posts batches every 2 s to `POST /api/v1/internal/telemetry` with an **ingest token** (scope `telemetry.write` only).
- **Hub:**
  - Device and SensorReading models; a unique constraint on (device_id, ts).
  - Update `last_seen` and battery.
  - Link a reading to `assigned_shipment_id` only while that shipment is ASSIGNED, PICKED_UP or IN_TRANSIT.
  - Emit `coldchain.reading`.
  - `GET /devices` and `POST /devices/{id}/assign`.
- **delivery-web:** a device column and assign action on the Fleet screen and the shipment detail.
- **`scripts/simulate_telemetry.py --device cb-01 --profile normal|excursion|silent [--interval 10]`.** Profiles:
  - normal: 3.5–5.5 °C.
  - excursion: normal for 1 min, then 9.1 and 9.4 °C, then back to normal.
  - silent: stops publishing after 1 min.
- **CI:** compile the firmware with `pio run` (cached).

## Out of scope
Cold-chain rules, alerts and charts (S15).

## Acceptance criteria
- [ ] Simulator → readings appear in the database within 5 s; replaying the same messages adds no duplicates.
- [ ] Readings link to a shipment only while the device is assigned and the shipment is active.
- [ ] The ingest token is refused on every other endpoint (test).
- [ ] The firmware compiles in CI. A manual hardware test with the real probe is recorded in PROGRESS.md, or marked pending if hardware hasn't arrived.

## Verify
```
make up && make worker   # plus: (cd services/iot-ingest && uv run python -m iot_ingest)
python scripts/simulate_telemetry.py --device cb-01 --profile normal
make test-hub && (cd services/iot-ingest && uv run pytest)
(cd firmware/cold-box && pio run)
```
