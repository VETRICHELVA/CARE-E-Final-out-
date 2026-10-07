# iot-ingest

Subscribes to cold-box telemetry on the Mosquitto broker (paho-mqtt) and forwards readings to the hub, which stores them and applies the cold-chain rules.

- Subscribes to `careE/devices/+/telemetry` and validates each message (`app/telemetry.py`). The payload's `device_id` must match the topic.
- De-duplicates in memory on (device_id, ts) (`app/batcher.py`). The hub's unique (device_id, ts) is the final guard, so a replay after a restart is harmless too.
- Every 2 s, posts the queue to `POST /api/v1/internal/telemetry`, at most 500 readings per request, with `Authorization: Bearer $INGEST_TOKEN` (scope `telemetry.write` only; `app/hub.py`). While the hub is unreachable, returns 5xx, 429, 401 or 403, readings stay queued (up to 10,000, oldest dropped first). A batch the hub rejects (e.g. 422) is dropped and logged.
- Readings from a device_id with no hub Device are reported as `unknown_devices` and not stored. `make seed` registers `cb-01` for SwiftMed Logistics.

Run: `cp .env.example .env` (same `INGEST_TOKEN` as the hub), then `make ingest` from the repo root (or `uv run python -m app` here). Try it with `uv run scripts/simulate_telemetry.py --device cb-01 --profile normal`.

Test: `uv run pytest` (no broker needed: a fake MQTT client and an `httpx.MockTransport` hub).
