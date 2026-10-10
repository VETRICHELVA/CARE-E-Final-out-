from datetime import UTC, datetime, timedelta

from app.domain import metrics

T0 = datetime(2026, 10, 9, 6, tzinfo=UTC)


def test_median_minutes() -> None:
    assert metrics.median_minutes([]) is None
    pairs = [(T0, T0 + timedelta(minutes=m)) for m in (10, 30, 20, 50)]
    assert metrics.median_minutes(pairs) == 25.0
    assert metrics.median_minutes([(T0, T0 - timedelta(minutes=5))]) == 0.0


def test_share() -> None:
    assert metrics.share(0, 0) is None
    assert metrics.share(1, 3) == 0.3333
    assert metrics.share(3, 3) == 1.0


def test_cost_avoided_needs_a_recorded_supplier_price() -> None:
    assert metrics.cost_avoided_paise(805, 1400) == 1_127_000
    assert metrics.cost_avoided_paise(805, None) is None


def test_units_saved_from_expiry() -> None:
    lot = metrics.HeldLot
    # Only posted batches count, each up to what was posted, all up to what was accepted.
    assert metrics.units_saved_from_expiry(300, [lot(300, 300)]) == 300
    assert metrics.units_saved_from_expiry(850, [lot(850, 300)]) == 300
    assert metrics.units_saved_from_expiry(200, [lot(850, 300)]) == 200
    assert metrics.units_saved_from_expiry(500, [lot(250, None), lot(250, 100)]) == 100
    assert metrics.units_saved_from_expiry(0, [lot(250, 100)]) == 0
