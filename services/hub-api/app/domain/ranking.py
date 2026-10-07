"""Ranking of eligible candidates (business-rules.md §5).

CRITICAL: earliest ETA -> highest reliability -> lowest landed cost.
ROUTINE: lowest landed cost -> near-expiry first -> highest reliability.
A candidate's landed cost is for what it would supply, min(qty, shortfall): the figure the
match run stores and shows, so the order can be explained from the displayed numbers.
Ties end on `key`, so a run is always reproducible."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from app.domain import config, costing


@dataclass(frozen=True)
class Option:
    """A candidate as ranking and resolution see it."""

    key: str  # the caller's handle, e.g. the source org id
    hospital: bool
    qty: int  # transferable (hospital) or available (supplier)
    eta_hours: float
    reliability: int
    near_expiry: bool
    lots: tuple[tuple[int, int], ...]  # (qty, unit price paise), in the order stock is taken
    transport_paise: int

    def landed_cost(self, qty: int) -> int:
        return costing.landed_cost_paise(self.lots, qty, self.transport_paise, self.hospital)

    def cost(self, need: int) -> int:
        """Landed cost of what this source would supply toward `need`."""
        return self.landed_cost(min(self.qty, need))


def is_near_expiry(expiry_date: date, today: date) -> bool:
    return (expiry_date - today).days <= config.NEAR_EXPIRY_DAYS


def rank(options: Iterable[Option], need: int, critical: bool) -> list[Option]:
    def critical_key(o: Option) -> tuple[float, int, int, str]:
        return (o.eta_hours, -o.reliability, o.cost(need), o.key)

    def routine_key(o: Option) -> tuple[int, bool, int, str]:
        return (o.cost(need), not o.near_expiry, -o.reliability, o.key)

    return sorted(options, key=critical_key if critical else routine_key)
