from datetime import date, timedelta

from app.domain.ranking import Option, is_near_expiry, rank

NEED = 850


def opt(
    key: str,
    *,
    eta: float = 10.0,
    reliability: int = 70,
    unit: int = 1000,
    near: bool = False,
    qty: int = NEED,
    hospital: bool = False,
) -> Option:
    return Option(key, hospital, qty, eta, reliability, near, ((qty, unit),), transport_paise=0)


def order(options: list[Option], critical: bool) -> list[str]:
    return [o.key for o in rank(options, NEED, critical)]


# CRITICAL: earliest ETA -> highest reliability -> lowest landed cost


def test_critical_earliest_eta_first() -> None:
    fast = opt("fast", eta=2, reliability=50, unit=2000)
    slow = opt("slow", eta=24, reliability=90, unit=1000)
    assert order([slow, fast], critical=True) == ["fast", "slow"]


def test_critical_same_eta_then_highest_reliability() -> None:
    low, high = opt("low", reliability=60, unit=1000), opt("high", reliability=80, unit=2000)
    assert order([low, high], critical=True) == ["high", "low"]


def test_critical_same_eta_and_reliability_then_lowest_landed_cost() -> None:
    assert order([opt("dear", unit=2000), opt("cheap", unit=1000)], critical=True) == [
        "cheap",
        "dear",
    ]


# ROUTINE: lowest landed cost -> near-expiry first -> highest reliability


def test_routine_lowest_landed_cost_first() -> None:
    cheap = opt("cheap", unit=1000, eta=60, reliability=50)
    dear = opt("dear", unit=2000, eta=2, reliability=90, near=True)
    assert order([dear, cheap], critical=False) == ["cheap", "dear"]


def test_routine_same_cost_then_near_expiry_first() -> None:
    later, near = opt("later", reliability=90), opt("near", near=True, reliability=50)
    assert order([later, near], critical=False) == ["near", "later"]


def test_routine_same_cost_and_expiry_band_then_highest_reliability() -> None:
    assert order([opt("low", reliability=60), opt("high", reliability=80)], critical=False) == [
        "high",
        "low",
    ]


def test_a_full_tie_ends_on_the_key_so_runs_are_reproducible() -> None:
    for critical in (True, False):
        assert order([opt("b"), opt("a")], critical) == ["a", "b"]


def test_landed_cost_counts_only_what_the_source_would_supply() -> None:
    big = opt("big", qty=5000, unit=1000)
    exact = opt("exact", qty=850, unit=1001)
    assert big.cost(NEED) == 850_000
    assert order([exact, big], critical=False) == ["big", "exact"]


def test_hospital_landed_cost_includes_the_handling_fee() -> None:
    # Same unit price: the hospital's 2% handling fee makes the supplier cheaper.
    hospital, supplier = opt("hospital", hospital=True), opt("supplier")
    assert order([hospital, supplier], critical=False) == ["supplier", "hospital"]


def test_scenario_1_suppliers() -> None:
    x = opt("X", eta=68, unit=1400)
    y = opt("Y", eta=24, unit=2800)
    assert order([x, y], critical=True) == ["Y", "X"]
    assert order([x, y], critical=False) == ["X", "Y"]


def test_near_expiry_is_within_90_days() -> None:
    today = date(2026, 10, 6)
    assert is_near_expiry(today + timedelta(days=90), today)
    assert not is_near_expiry(today + timedelta(days=91), today)
