"""business-rules.md §11, pure: excursion, recovery and silent-device findings."""

from datetime import UTC, datetime, timedelta

from app.domain.coldchain import (
    Band,
    ColdChainEventType,
    Reading,
    Severity,
    State,
    describe,
    evaluate,
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
    assert silence_start(T0 - timedelta(hours=1), T0) == T0 - timedelta(hours=1)
    assert silence_start(None, None) is None
    assert silent_finding(None, T0, None) is None


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
