from dataclasses import dataclass
from datetime import date, timedelta

import pytest

from app.domain import config
from app.domain.forecast import (
    Day,
    covers,
    expiry_risk,
    from_today,
    horizon_end,
    lead_time_days,
    reorder_qty,
    stockout_date,
    usable_stock,
    usage,
)

TODAY = date(2026, 10, 8)


def flat(rate: float, n: int, start: date = TODAY) -> list[Day]:
    return [Day(start + timedelta(i), rate, rate - 1, rate + 1) for i in range(n)]


@dataclass
class B:
    on_hand: int
    expiry_date: date
    reserved: int = 0
    allocated: int = 0
    safety_stock: int = 0
    quarantined: int = 0


def test_stockout_is_the_first_day_cumulative_forecast_exceeds_usable_stock() -> None:
    # Scenario 3, Hospital E: 120 usable at 30 a day lasts today and the next three days.
    assert stockout_date(flat(30, 30), 120, TODAY) == TODAY + timedelta(4)
    assert stockout_date(flat(30, 30), 119, TODAY) == TODAY + timedelta(3)
    assert stockout_date(flat(30, 30), 0, TODAY) == TODAY
    assert stockout_date(flat(30, 30), 900, TODAY) is None  # never within the forecast
    assert stockout_date(flat(0, 30), 0, TODAY) is None  # nothing used: nothing runs out


def test_days_before_today_do_not_count() -> None:
    days = flat(30, 35, TODAY - timedelta(5))
    assert from_today(days, TODAY)[0].date == TODAY
    assert stockout_date(days, 120, TODAY) == TODAY + timedelta(4)


def test_expiry_risk_scenario_3_hospital_b() -> None:
    """On hand 1,000, safety 200, expiry +55 days, forecast usage before expiry 500 -> 300."""
    rate = 500 / 55
    risk = expiry_risk(flat(rate, 60), TODAY, TODAY + timedelta(55), 1000, 200)
    assert risk is not None
    assert risk.usage_before_expiry == pytest.approx(500)
    assert risk.excess == 300


def test_expiry_risk_counts_up_to_the_day_before_expiry() -> None:
    risk = expiry_risk(flat(10, 60), TODAY, TODAY + timedelta(10), 100, 0)
    assert risk is None  # 10 days x 10 = 100: nothing left over
    risk = expiry_risk(flat(10, 60), TODAY, TODAY + timedelta(10), 101, 0)
    assert risk is not None and risk.excess == 1


def test_no_expiry_risk_when_used_up_expired_or_not_covered() -> None:
    assert expiry_risk(flat(30, 60), TODAY, TODAY + timedelta(55), 1000, 200) is None
    assert expiry_risk(flat(1, 60), TODAY, TODAY, 1000, 0) is None  # expired today
    # The forecast stops at day 30, so usage up to day 55 is unknown: not judged.
    assert expiry_risk(flat(1, 30), TODAY, TODAY + timedelta(55), 1000, 0) is None


def test_covers_and_usage() -> None:
    days = flat(2, 10)
    assert covers(days, TODAY, TODAY + timedelta(10))
    assert not covers(days, TODAY, TODAY + timedelta(11))
    assert covers(days, TODAY, TODAY)
    assert usage(days, TODAY, TODAY + timedelta(3)) == 6
    assert usage(days[1:], TODAY, TODAY + timedelta(3)) is None  # today missing


def test_usable_stock_deducts_everything_but_safety_stock() -> None:
    batches = [
        (B(on_hand=100, reserved=10, allocated=5, quarantined=5, safety_stock=50,
           expiry_date=TODAY + timedelta(30)), 20),
        (B(on_hand=40, expiry_date=TODAY), 0),  # expired today: not usable
        (B(on_hand=10, reserved=20, expiry_date=TODAY + timedelta(9)), 0),  # never below 0
    ]  # fmt: skip
    assert usable_stock(batches, TODAY) == 60


def test_reorder_suggestion() -> None:
    # 7 days x 30 + safety 50 - usable 120 = 140
    assert reorder_qty(flat(30, 30), TODAY, 7, 50, 120) == 140
    assert reorder_qty(flat(30, 30), TODAY, 2, 0, 500) == 0
    assert reorder_qty(flat(30.2, 30), TODAY, 1, 0, 30) == 1  # rounded up
    assert reorder_qty(flat(30, 5), TODAY, 7, 0, 0) is None  # forecast shorter than lead time


def test_lead_time_is_the_shortest_offer_in_days_rounded_up() -> None:
    assert lead_time_days([66, 22]) == 1
    assert lead_time_days([25]) == 2
    assert lead_time_days([0]) == 1
    assert lead_time_days([]) == config.FORECAST_DEFAULT_LEAD_TIME_DAYS


def test_horizon_reaches_the_latest_expiry_within_limits() -> None:
    assert horizon_end(TODAY, []) == TODAY + timedelta(29)
    assert horizon_end(TODAY, [TODAY + timedelta(10)]) == TODAY + timedelta(29)
    assert horizon_end(TODAY, [TODAY + timedelta(55), TODAY - timedelta(1)]) == TODAY + timedelta(
        54
    )
    assert horizon_end(TODAY, [TODAY + timedelta(5000)]) == TODAY + timedelta(
        config.FORECAST_MAX_HORIZON_DAYS - 1
    )
