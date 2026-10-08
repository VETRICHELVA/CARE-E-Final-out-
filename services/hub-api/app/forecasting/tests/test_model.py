"""The statistical model (apps-ai-iot.md, Forecasting): Holt-Winters with weekly seasonality,
a 28-day moving average under 60 days of history, and reproducible numbers."""

import runpy
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.forecasting import model

SEED_DIR = Path(__file__).resolve().parents[5] / "scripts" / "seed"
TODAY = date(2026, 10, 8)


@pytest.fixture(scope="module")
def generator() -> dict[str, Any]:
    return runpy.run_path(str(SEED_DIR / "consumption.py"))


def test_the_same_history_gives_the_same_numbers(generator: dict[str, Any]) -> None:
    history = generator["series"]("Hospital A", "IV-CAN-20G", TODAY)
    first = TODAY - timedelta(days=len(history))
    runs = [model.forecast(history, first, TODAY, TODAY + timedelta(days=29)) for _ in range(2)]
    assert runs[0] == runs[1]
    assert runs[0].model_version == model.HOLT_WINTERS
    assert [d.date for d in runs[0].days] == [TODAY + timedelta(days=i) for i in range(30)]
    assert all(0 <= d.lower <= d.predicted <= d.upper for d in runs[0].days)
    # Weekly seasonality: Saturday and Sunday forecasts sit below the weekdays.
    weekend = [d.predicted for d in runs[0].days if d.date.weekday() >= 5]
    weekdays = [d.predicted for d in runs[0].days if d.date.weekday() < 5]
    assert max(weekend) < min(weekdays)


def test_under_60_days_of_history_is_a_28_day_moving_average() -> None:
    history = [10.0] * 31 + [20.0, 40.0] * 14  # 59 days; the last 28 average 30
    first = TODAY - timedelta(days=len(history))
    result = model.forecast(history, first, TODAY, TODAY + timedelta(days=9))
    assert result.model_version == model.MOVING_AVERAGE
    assert [d.predicted for d in result.days] == [30.0] * 10
    # 95% interval: mean +- 1.96 x the window's standard deviation (10), floored at 0.
    assert result.days[0].lower == pytest.approx(30 - 19.6, abs=0.01)
    assert result.days[0].upper == pytest.approx(30 + 19.6, abs=0.01)


def test_60_days_or_more_is_holt_winters() -> None:
    history = [float(30 if (i % 7) < 5 else 25) for i in range(60)]
    first = TODAY - timedelta(days=60)
    result = model.forecast(history, first, TODAY, TODAY + timedelta(days=6))
    assert result.model_version == model.HOLT_WINTERS
    assert sum(d.predicted for d in result.days) == pytest.approx(200, abs=1)


def test_dates_must_follow_the_history() -> None:
    with pytest.raises(ValueError):
        model.forecast([], TODAY, TODAY, TODAY)
    with pytest.raises(ValueError):
        model.forecast([1.0], TODAY, TODAY, TODAY)  # history ends on the start date
    # Dates between the history and `start` are forecast but not returned.
    result = model.forecast([5.0] * 10, TODAY - timedelta(days=12), TODAY, TODAY)
    assert [d.date for d in result.days] == [TODAY]


def test_generator_is_deterministic_and_follows_the_spec(generator: dict[str, Any]) -> None:
    series = generator["series"]
    assert series("Hospital C", "INJ-SYR-5", TODAY) == series("Hospital C", "INJ-SYR-5", TODAY)
    assert len(generator["TOP_PRODUCTS"]) == 15
    days = generator["history_days"](TODAY)
    assert (len(days), days[0], days[-1]) == (365, TODAY - timedelta(365), TODAY - timedelta(1))
    # Weekdays about 1.2x weekends, before noise and spikes.
    assert generator["WEEKDAY"] / generator["WEEKEND"] == pytest.approx(1.2)
    # Scenario 3: B uses about 500 in any 55 days; E's 120 last exactly 4 days.
    b = series("Hospital B", "IV-CAN-20G", TODAY)
    assert all(495 <= sum(b[i : i + 55]) <= 505 for i in range(0, 365 - 55))
    e = series("Hospital E", "IV-CAN-20G", TODAY)
    assert all(sum(e[i : i + 4]) <= 120 < sum(e[i : i + 5]) for i in range(0, 360))
    assert sum(e) / len(e) == pytest.approx(30, abs=2)  # "averaging about 30 per day"
