import json
from datetime import UTC, datetime
from typing import Any

import pytest
from app.telemetry import TOPIC_FILTER, Reading, parse

TOPIC = "careE/devices/cb-01/telemetry"


def payload(**overrides: Any) -> bytes:
    body = {"device_id": "cb-01", "ts": "2026-11-20T10:15:00Z", "temp_c": 4.31, "battery": 82}
    return json.dumps(body | overrides).encode()


def test_parses_the_firmware_message() -> None:
    reading = parse(TOPIC, payload())
    assert reading == Reading(
        device_id="cb-01", ts=datetime(2026, 11, 20, 10, 15, tzinfo=UTC), temp_c=4.31, battery=82
    )
    assert reading is not None
    assert reading.to_hub() == {
        "device_id": "cb-01",
        "ts": "2026-11-20T10:15:00Z",
        "temp_c": 4.31,
        "battery": 82,
    }


def test_battery_may_be_omitted() -> None:
    body = json.dumps({"device_id": "cb-01", "ts": "2026-11-20T10:15:00Z", "temp_c": 4.0})
    reading = parse(TOPIC, body.encode())
    assert reading is not None
    assert reading.battery is None
    assert "battery" not in reading.to_hub()


def test_topic_filter_matches_every_device() -> None:
    assert TOPIC_FILTER == "careE/devices/+/telemetry"


@pytest.mark.parametrize(
    ("topic", "body"),
    [
        ("careE/devices/cb-01/status", payload()),
        ("careE/devices/cb-01/telemetry/extra", payload()),
        ("careE/devices/cb-02/telemetry", payload()),  # payload says cb-01
        (TOPIC, b"not json"),
        (TOPIC, b"\xff\xfe"),
        (TOPIC, b"[]"),
        (TOPIC, payload(temp_c=126)),
        (TOPIC, payload(temp_c="warm")),
        (TOPIC, payload(battery=101)),
        (TOPIC, payload(ts="2026-11-20T10:15:00")),  # no timezone
        (TOPIC, payload(ts="yesterday")),
        (TOPIC, payload(device_id=None)),
    ],
)
def test_rejects_anything_else(topic: str, body: bytes) -> None:
    assert parse(topic, body) is None


def test_ignores_extra_fields() -> None:
    reading = parse(TOPIC, payload(rssi=-60))
    assert reading is not None
    assert "rssi" not in reading.to_hub()
