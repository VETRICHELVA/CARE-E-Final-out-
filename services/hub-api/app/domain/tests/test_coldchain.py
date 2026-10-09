"""business-rules.md §11, pure: excursion, recovery and silent-device findings."""

import random
from datetime import UTC, datetime, timedelta

from app.domain.coldchain import (
    Band,
    ColdChainEventType,
    Finding,
    Reading,
    Severity,
    State,
    describe,
    evaluate,
    on_arrival,
    resume_from,
    silence_start,
    silent_finding,
)

E = ColdChainEventType
T0 = datetime(2026, 11, 20, 10, 15, tzinfo=UTC)
FRIDGE = Band(2.0, 8.0)  # Rapid Diagnostic Kit, 2–8 °C


def readings(*temps: float, start: int = 0) -> list[Reading]:
    return [Reading(T0 + timedelta(seconds=10 * (start + i)), t) for i, t in enumerate(temps)]


def kinds(findings: list) -> list[str]:  # type: ignore[type-arg]
    return [f.type for f in findings]


def test_a_single_out_of_range_spike_is_not_an_excursion() -> None:
    assert evaluate(FRIDGE, [], readings(4.1, 9.5, 4.2, 12.0, 4.0), State()) == []


def test_two_consecutive_out_of_range_readings_are_an_excursion() -> None:
    """Scenario 2: near 4 °C, then 9.1 and 9.4 °C."""
    (found,) = evaluate(FRIDGE, [], readings(4.1, 4.3, 9.1, 9.4), State())
    assert found.type == E.EXCURSION
    assert found.severity == Severity.ALERT
    assert (found.observed_value, found.threshold) == (9.4, 8.0)
    assert found.ts == T0 + timedelta(seconds=30)
    assert [r.temp_c for r in found.readings] == [9.1, 9.4]


def test_the_run_carries_on_from_the_previous_batch() -> None:
    """The ingest posts every 2 s, so the two readings usually arrive in separate batches."""
    (found,) = evaluate(FRIDGE, readings(4.0, 9.1), readings(9.4, start=2), State())
    assert (found.type, found.observed_value) == (E.EXCURSION, 9.4)
    # Only the last reading before the batch counts.
    assert evaluate(FRIDGE, readings(9.1, 4.0), readings(9.4, start=2), State()) == []


def test_below_the_band_is_an_excursion_against_the_lower_bound() -> None:
    (found,) = evaluate(FRIDGE, [], readings(1.5, 0.8), State())
    assert (found.type, found.observed_value, found.threshold) == (E.EXCURSION, 0.8, 2.0)


def test_the_bounds_are_in_range() -> None:
    assert evaluate(FRIDGE, [], readings(8.0, 2.0, 8.0, 2.0), State()) == []


def test_no_second_excursion_while_one_is_running() -> None:
    found = evaluate(FRIDGE, [], readings(9.1, 9.4, 10.0, 11.0, 9.0), State())
    assert kinds(found) == [E.EXCURSION]
    assert evaluate(FRIDGE, readings(9.4), readings(9.6, 9.8, start=1), State(True, 8.0)) == []


def test_two_readings_back_in_range_recover_and_the_excursion_stays_on_record() -> None:
    found = evaluate(FRIDGE, [], readings(4.0, 9.1, 9.4, 5.0, 4.6, 4.4), State())
    assert kinds(found) == [E.EXCURSION, E.RECOVERED]
    recovered = found[1]
    assert (recovered.observed_value, recovered.threshold) == (4.6, 8.0)
    assert recovered.severity == Severity.INFO


def test_one_reading_back_in_range_does_not_recover() -> None:
    assert evaluate(FRIDGE, readings(9.4), readings(5.0, 9.2, 4.9, start=1), State(True, 8.0)) == []


def test_recovery_carries_on_from_the_previous_batch() -> None:
    (found,) = evaluate(FRIDGE, readings(9.4, 5.0), readings(4.6, start=2), State(True, 8.0))
    assert (found.type, found.threshold) == (E.RECOVERED, 8.0)


def test_a_new_excursion_after_recovery() -> None:
    found = evaluate(FRIDGE, [], readings(9.1, 9.4, 5.0, 4.6, 9.0, 9.2), State())
    assert kinds(found) == [E.EXCURSION, E.RECOVERED, E.EXCURSION]


def test_readings_are_taken_in_timestamp_order() -> None:
    batch = list(reversed(readings(4.0, 9.1, 4.2, 9.4)))
    assert evaluate(FRIDGE, [], batch, State()) == []


def test_a_product_without_a_band_has_no_rule() -> None:
    assert evaluate(Band(None, None), [], readings(30.0, 31.0, 32.0), State()) == []


def test_a_single_bound_limits_one_side() -> None:
    frozen = Band(None, -15.0)
    assert evaluate(frozen, [], readings(-40.0, -30.0), State()) == []
    (found,) = evaluate(frozen, [], readings(-10.0, -12.0), State())
    assert found.threshold == -15.0


def test_the_run_length_is_tunable() -> None:
    assert evaluate(FRIDGE, [], readings(9.1, 9.4), State(), consecutive=3) == []
    (found,) = evaluate(FRIDGE, readings(9.1, 9.4), readings(9.6, start=2), State(), consecutive=3)
    assert len(found.readings) == 3


# --- DEVICE_SILENT ----------------------------------------------------------------------------


def test_silent_for_two_minutes_raises_device_silent() -> None:
    last = T0
    assert silent_finding(last, T0 + timedelta(seconds=119), None) is None
    found = silent_finding(last, T0 + timedelta(seconds=150), None)
    assert found is not None
    assert (found.type, found.severity) == (E.DEVICE_SILENT, Severity.WARNING)
    assert (found.observed_value, found.threshold) == (150.0, 120.0)
    assert found.ts == T0 + timedelta(seconds=150)
    assert found.readings == ()


def test_device_silent_is_raised_once_per_silence() -> None:
    first = T0 + timedelta(minutes=3)
    assert silent_finding(T0, first + timedelta(minutes=5), first) is None
    # The device sent again after that event, then fell silent again.
    again = first + timedelta(minutes=1)
    found = silent_finding(again, again + timedelta(minutes=2), first)
    assert found is not None and found.observed_value == 120.0


def test_a_device_that_never_sent_is_silent_since_the_shipment_went_in_transit() -> None:
    assert silence_start(None, T0) == T0
    assert silence_start(None, None) is None
    assert silent_finding(None, T0, None) is None


def test_silence_counts_from_the_later_of_the_last_reading_and_in_transit() -> None:
    """§11 "silent for 2 minutes while IN_TRANSIT": a box last heard from yesterday is not
    silent the moment the shipment goes IN_TRANSIT at 10:00:00."""
    in_transit = datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC)
    yesterday = in_transit - timedelta(days=1)
    since = silence_start(yesterday, in_transit)
    assert since == in_transit
    assert silent_finding(since, in_transit + timedelta(seconds=30), None) is None
    assert silent_finding(since, in_transit + timedelta(seconds=119), None) is None
    found = silent_finding(since, in_transit + timedelta(minutes=2), None)
    assert found is not None and found.observed_value == 120.0
    later = silent_finding(since, in_transit + timedelta(minutes=5), None)
    assert later is not None and later.observed_value == 300.0
    # A reading during the trip moves the start on.
    during = in_transit + timedelta(minutes=1)
    assert silence_start(during, in_transit) == during
    assert silence_start(during, None) == during


# --- the audit wording states only what the readings show ----------------------------------


def test_the_reasons_name_the_readings_and_the_band() -> None:
    excursion, recovered = evaluate(FRIDGE, [], readings(9.1, 9.4, 5.0, 4.6), State())
    assert describe(excursion, FRIDGE, "cb-01") == (
        "2 consecutive readings from cb-01 outside 2–8 °C: "
        "9.1 °C at 2026-11-20 10:15:00 UTC, 9.4 °C at 2026-11-20 10:15:10 UTC."
    )
    assert describe(recovered, FRIDGE, "cb-01") == (
        "2 consecutive readings from cb-01 back within 2–8 °C: "
        "5 °C at 2026-11-20 10:15:20 UTC, 4.6 °C at 2026-11-20 10:15:30 UTC. "
        "The excursion stays on record."
    )
    silent = silent_finding(T0, T0 + timedelta(seconds=150), None)
    assert silent is not None
    assert describe(silent, FRIDGE, "cb-01", T0) == (
        "No reading received from cb-01 since 2026-11-20 10:15:00 UTC (150 s; the limit is 120 s)."
    )
    assert describe(silent, FRIDGE, "cb-01", T0, in_transit_at=T0) == (
        "No reading received from cb-01 since the shipment went IN_TRANSIT at "
        "2026-11-20 10:15:00 UTC (150 s; the limit is 120 s)."
    )


# --- late and out-of-order batches: the events on record stand ---------------------------------


def at(i: int) -> datetime:
    return T0 + timedelta(seconds=10 * i)


class Shipment:
    """The hub's side of §11 in memory: stores each batch (a replay stores nothing), runs
    `on_arrival` on it and records what it finds, as `app.coldchain.service` does."""

    def __init__(self, band: Band = FRIDGE) -> None:
        self.band = band
        self.stored: dict[datetime, Reading] = {}
        self.events: list[Finding] = []

    @property
    def state(self) -> State:
        if not self.events:
            return State()
        last = self.events[-1]
        if last.type == E.EXCURSION:
            return State(True, last.threshold, last.ts)
        return State(as_of=last.ts)

    def send(self, *readings: tuple[int, float]) -> list[Finding]:
        new = [Reading(at(i), t) for i, t in readings if at(i) not in self.stored]
        self.stored.update({r.ts: r for r in new})
        found = on_arrival(self.band, list(self.stored.values()), new, self.state)
        self.events.extend(found)
        return found


def summary(findings: list[Finding]) -> list[tuple[str, datetime]]:
    return [(f.type, f.ts) for f in findings]


def test_a_late_batch_completes_an_excursion() -> None:
    """Review input (a): t3 = 9.4 arrives first, then t1 = 4.0 and t2 = 9.1. In timestamp
    order t2 and t3 are an excursion."""
    box = Shipment()
    assert box.send((3, 9.4)) == []
    (found,) = box.send((1, 4.0), (2, 9.1))
    assert (found.type, found.ts, found.observed_value, found.threshold) == (
        E.EXCURSION,
        at(3),
        9.4,
        8.0,
    )
    assert [r.temp_c for r in found.readings] == [9.1, 9.4]


def test_late_in_range_readings_before_an_excursion_do_not_recover_it() -> None:
    """Review input (b): EXCURSION at t5, then in-range readings dated t1 and t2 arrive. No
    RECOVERED dated before the excursion; the real recovery at t6/t7 is recorded once."""
    box = Shipment()
    assert summary(box.send((4, 9.1), (5, 9.4))) == [(E.EXCURSION, at(5))]
    assert box.send((1, 4.0), (2, 4.1)) == []
    assert summary(box.send((6, 5.0), (7, 4.6))) == [(E.RECOVERED, at(7))]
    assert summary(box.events) == [(E.EXCURSION, at(5)), (E.RECOVERED, at(7))]


def test_a_late_excursion_before_the_events_on_record_is_not_raised() -> None:
    """Review input (c): after EXCURSION and RECOVERED, two out-of-range readings dated
    before them arrive. The events on record stand; no second EXCURSION."""
    box = Shipment()
    box.send((4, 9.1), (5, 9.4), (6, 5.0), (7, 4.6))
    assert summary(box.events) == [(E.EXCURSION, at(5)), (E.RECOVERED, at(7))]
    assert box.send((1, 9.2), (2, 9.5)) == []
    assert box.send((1, 9.2), (2, 9.5), (4, 9.1)) == []  # a replay
    assert summary(box.events) == [(E.EXCURSION, at(5)), (E.RECOVERED, at(7))]


def test_a_late_batch_after_the_last_event_is_read_in_timestamp_order() -> None:
    """Late readings dated after the last event are merged with those already stored."""
    box = Shipment()
    box.send((1, 9.1), (2, 9.4))  # EXCURSION at t2
    assert box.send((4, 4.6)) == []
    (found,) = box.send((3, 5.0))
    assert (found.type, found.ts) == (E.RECOVERED, at(4))


def test_a_reach_back_batch_resumes_after_the_last_event() -> None:
    state = State(True, 8.0, at(5))
    assert resume_from(state, at(7)) == (at(7), True)
    assert resume_from(state, at(5)) == (at(5), False)
    assert resume_from(state, at(1)) == (at(5), False)
    assert resume_from(State(), at(1)) == (at(1), True)


# Scenario 2 and then some: EXCURSION at t3, RECOVERED at t6, a single spike at t8, an
# excursion below the band at t11 and RECOVERED at t13.
TEMPS = [4.1, 4.3, 9.1, 9.4, 9.6, 5.0, 4.6, 4.4, 9.0, 4.2, 1.5, 0.8, 4.0, 4.1]
EXPECTED = [
    (E.EXCURSION, at(3)),
    (E.RECOVERED, at(6)),
    (E.EXCURSION, at(11)),
    (E.RECOVERED, at(13)),
]


def test_the_expected_events_are_those_of_the_readings_in_timestamp_order() -> None:
    whole = evaluate(FRIDGE, [], [Reading(at(i), t) for i, t in enumerate(TEMPS)], State())
    assert summary(whole) == EXPECTED


def deliver(batches: list[list[int]]) -> tuple[Shipment, bool]:
    """Sends `batches` (indices into TEMPS); also says whether any batch reached back to or
    before the last EXCURSION or RECOVERED on record when it arrived."""
    box, reached_back = Shipment(), False
    for batch in batches:
        as_of = box.state.as_of
        if as_of is not None and min(at(i) for i in batch) <= as_of:
            reached_back = True
        box.send(*((i, TEMPS[i]) for i in batch))
    return box, reached_back


def test_the_same_readings_in_any_batching_and_order_give_the_same_events() -> None:
    """Property: whatever the batching and arrival order, the events are those of the readings
    in timestamp order, unless a batch reaches back to or before an event already on record.
    Even then, what is recorded never contradicts itself: EXCURSION and RECOVERED alternate,
    each dated after the one before, and replaying every batch adds nothing."""
    rng = random.Random(15)
    n = len(TEMPS)
    deliveries = [
        [list(range(n))],
        [[i] for i in range(n)],
        [list(reversed(range(n)))],
        [[i + 1, i] for i in range(0, n, 2)],
        [[1, 0], [3], [2], [6], [4], [5], [9, 8, 7], [12], [10, 11], [13]],
    ]
    for _ in range(500):
        order = list(range(n))
        rng.shuffle(order)
        cuts = sorted(rng.sample(range(1, n), rng.randint(0, n - 1)))
        deliveries.append([order[a:b] for a, b in zip([0, *cuts], [*cuts, n], strict=True)])
    exact = 0
    for batches in deliveries:
        box, reached_back = deliver(batches)
        if not reached_back:
            exact += 1
            assert summary(box.events) == EXPECTED, batches
        types = [f.type for f in box.events]
        assert types == [E.EXCURSION, E.RECOVERED] * (len(types) // 2) + [E.EXCURSION] * (
            len(types) % 2
        ), batches
        stamps = [f.ts for f in box.events]
        assert stamps == sorted(set(stamps)), batches
        for batch in batches:
            assert box.send(*((i, TEMPS[i]) for i in batch)) == []
    assert exact >= 5 + 50  # the fixed deliveries and plenty of random ones are exact
