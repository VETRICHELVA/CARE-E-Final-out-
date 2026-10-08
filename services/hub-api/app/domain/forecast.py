"""What a stored forecast means for a hospital's stock (apps-ai-iot.md, Forecasting; S18).
Pure functions, no I/O: the statistics live in `app.forecasting.model`, the stored daily
forecasts in `Forecast` rows; these turn them into the predicted stock-out date, the reorder
suggestion and each batch's expiry-risk excess, against the stock recorded now.

A daily forecast covers one UTC date. "From today" means the forecast for today counts in
full: history ends yesterday, so nothing of today has been recorded yet."""

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

from app.domain import config
from app.domain.inventory import is_expired


@dataclass(frozen=True)
class Day:
    """One day's forecast consumption with its interval (Forecast row)."""

    date: date
    predicted: float
    lower: float
    upper: float


class StockBatch(Protocol):
    on_hand: int
    reserved: int
    allocated: int
    safety_stock: int
    quarantined: int
    expiry_date: date


def from_today(days: Iterable[Day], today: date) -> list[Day]:
    """The forecast days from `today` on, in date order."""
    return sorted((d for d in days if d.date >= today), key=lambda d: d.date)


def covers(days: Sequence[Day], today: date, end: date) -> bool:
    """True if `days` (from_today) has a forecast for every date in [today, end)."""
    span = (end - today).days
    return span <= 0 or (
        len(days) >= span and days[0].date == today and days[span - 1].date == end - timedelta(1)
    )


def usage(days: Sequence[Day], today: date, end: date) -> float | None:
    """Forecast consumption over [today, end); None if the forecast does not reach `end`."""
    if not covers(days, today, end):
        return None
    return sum(d.predicted for d in days if today <= d.date < end)


def usable_stock(batches: Iterable[tuple[StockBatch, int]], today: date) -> int:
    """Stock the hospital can still use itself: per unexpired batch, on hand less reserved,
    allocated, quarantined and active holds (`held`, counted as reserved, business-rules.md
    §2), never below 0. Safety stock is usable by its own hospital, so it is not deducted."""
    return sum(
        max(0, b.on_hand - b.reserved - b.allocated - b.quarantined - held)
        for b, held in batches
        if not is_expired(b, today)
    )


def stockout_date(days: Sequence[Day], usable: int, today: date) -> date | None:
    """The first date on which cumulative forecast consumption from today exceeds `usable`
    stock; None if it never does within the forecast."""
    total = 0.0
    for d in from_today(days, today):
        total += d.predicted
        if round(total, 6) > usable:
            return d.date
    return None


def lead_time_days(lead_time_hours: Iterable[int]) -> int:
    """Reorder lead time: the shortest supplier lead time for the product in whole days
    (rounded up), or the configured default when no supplier offers it."""
    hours = list(lead_time_hours)
    if not hours:
        return config.FORECAST_DEFAULT_LEAD_TIME_DAYS
    return max(1, math.ceil(min(hours) / 24))


def reorder_qty(
    days: Sequence[Day], today: date, lead_days: int, safety_stock: int, usable: int
) -> int | None:
    """forecast lead-time demand + safety stock - usable stock, rounded up, never below 0;
    None if the forecast does not cover the lead time."""
    demand = usage(from_today(days, today), today, today + timedelta(lead_days))
    if demand is None:
        return None
    return max(0, math.ceil(round(demand + safety_stock - usable, 6)))


@dataclass(frozen=True)
class ExpiryRisk:
    usage_before_expiry: float
    excess: int


def expiry_risk(
    days: Sequence[Day], today: date, expiry_date: date, on_hand: int, safety_stock: int
) -> ExpiryRisk | None:
    """A batch is at expiry risk when forecast usage before its expiry is less than its
    on_hand minus safety stock; the excess (rounded down) is what could go to the network.
    Usage counts from today up to the day before expiry (a batch counts as expired on its
    expiry date, business-rules.md §2). None when it is not at risk, is already expired, or
    the forecast does not reach its expiry date."""
    if expiry_date <= today:
        return None
    used = usage(from_today(days, today), today, expiry_date)
    if used is None:
        return None
    spare = on_hand - safety_stock - used
    excess = math.floor(round(spare, 6))  # a float sum of 500.00000000000006 is 500
    if spare <= 0 or excess <= 0:
        return None
    return ExpiryRisk(usage_before_expiry=used, excess=excess)


def horizon_end(today: date, expiries: Iterable[date]) -> date:
    """The last date a run forecasts: the 30-day horizon, or far enough to judge the latest
    unexpired batch (up to the configured maximum)."""
    latest = max((e for e in expiries if e > today), default=today)
    days = max(config.FORECAST_HORIZON_DAYS, (latest - today).days)
    return today + timedelta(min(days, config.FORECAST_MAX_HORIZON_DAYS) - 1)
