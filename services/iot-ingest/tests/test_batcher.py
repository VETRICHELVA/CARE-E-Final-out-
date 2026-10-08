from datetime import UTC, datetime, timedelta, timezone

from app.batcher import Batcher
from app.telemetry import Reading

T0 = datetime(2026, 11, 20, 10, 15, tzinfo=UTC)


def reading(seconds: int = 0, device: str = "cb-01", temp: float = 4.0) -> Reading:
    return Reading(device_id=device, ts=T0 + timedelta(seconds=seconds), temp_c=temp)


def test_queues_each_device_and_ts_once() -> None:
    b = Batcher()
    assert b.add(reading(0))
    assert not b.add(reading(0, temp=9.9))  # same key, even with another value
    assert b.add(reading(0, device="cb-02"))
    assert b.add(reading(10))
    assert len(b) == 3


def test_the_same_instant_in_another_offset_is_a_duplicate() -> None:
    b = Batcher()
    ist = timezone(timedelta(hours=5, minutes=30))
    assert b.add(reading(0))
    assert not b.add(Reading(device_id="cb-01", ts=T0.astimezone(ist), temp_c=4.0))


def test_a_reading_already_sent_is_still_a_duplicate() -> None:
    b = Batcher()
    b.add(reading(0))
    assert b.take(10) == [reading(0)]
    assert not b.add(reading(0))  # e.g. the firmware replaying its offline buffer
    assert len(b) == 0


def test_take_returns_oldest_first_up_to_n() -> None:
    b = Batcher()
    for s in range(5):
        b.add(reading(s))
    assert [r.ts for r in b.take(3)] == [T0 + timedelta(seconds=s) for s in range(3)]
    assert len(b.take(3)) == 2
    assert b.take(3) == []


def test_put_back_returns_readings_to_the_front_in_order() -> None:
    b = Batcher()
    for s in range(4):
        b.add(reading(s))
    first = b.take(2)
    b.put_back(first)
    assert b.take(4) == [reading(s) for s in range(4)]


def test_a_full_queue_drops_the_oldest() -> None:
    b = Batcher(max_pending=3)
    for s in range(5):
        b.add(reading(s))
    assert b.take(10) == [reading(2), reading(3), reading(4)]
    assert b.dropped == 2


def test_the_dedup_window_forgets_the_oldest_keys() -> None:
    b = Batcher(dedup_window=2)
    for s in range(3):
        b.add(reading(s))
    b.take(10)
    assert b.add(reading(0))  # forgotten: the hub's unique (device_id, ts) catches it
    assert not b.add(reading(2))
