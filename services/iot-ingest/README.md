# iot-ingest

Subscribes to cold-box telemetry on the Mosquitto broker (paho-mqtt) and forwards readings to the hub, which applies the cold-chain rules. Set up with `uv sync`; lint and test from the repo root with `make lint` and `make test`. Application code arrives in S14.
