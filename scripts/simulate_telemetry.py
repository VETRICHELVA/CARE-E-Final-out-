# /// script
# requires-python = ">=3.12"
# dependencies = ["paho-mqtt>=2,<3"]
# ///
"""Publish fake cold-box telemetry: the same MQTT messages as firmware/cold-box.

Run with: uv run scripts/simulate_telemetry.py --device cb-01 --profile excursion --interval 2
"""

import argparse
import json
import random
import time
from collections.abc import Iterator
from datetime import UTC, datetime

NORMAL_RANGE = (3.5, 5.5)
EXCURSION = (9.1, 9.4)
WARMUP_S = 60  # excursion and silent behave normally for this long first
BATTERY = 82


def temperatures(profile: str, interval: float) -> Iterator[float]:
    """Readings to publish, one per interval. Endless except for "silent"."""

    def normal() -> float:
        return round(random.uniform(*NORMAL_RANGE), 2)

    for _ in range(max(1, round(WARMUP_S / interval))):
        yield normal()
    if profile == "silent":
        return
    if profile == "excursion":
        yield from EXCURSION
    while True:
        yield normal()


def main() -> None:
    # Imported here so tests can load temperatures() without paho installed.
    from paho.mqtt.client import Client
    from paho.mqtt.enums import CallbackAPIVersion

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", required=True)
    parser.add_argument("--profile", choices=["normal", "excursion", "silent"], default="normal")
    parser.add_argument("--interval", type=float, default=10, help="seconds between readings")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    args = parser.parse_args()
    if args.interval < 1:
        parser.error("--interval must be >= 1: ts has 1 s precision and is de-duplicated on")

    client = Client(CallbackAPIVersion.VERSION2, client_id=f"sim-{args.device}")
    client.connect(args.host, args.port)
    client.loop_start()
    topic = f"careE/devices/{args.device}/telemetry"
    try:
        for temp_c in temperatures(args.profile, args.interval):
            ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            payload = json.dumps(
                {"device_id": args.device, "ts": ts, "temp_c": temp_c, "battery": BATTERY}
            )
            client.publish(topic, payload).wait_for_publish()
            print(topic, payload, flush=True)
            time.sleep(args.interval)
        print(f"{args.device}: silent, stopped publishing", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
