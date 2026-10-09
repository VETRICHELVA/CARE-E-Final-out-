"""Network metrics for the platform admin (S20, api-and-events.md "Network metrics").
Pure functions over recorded figures, no I/O. Nothing here estimates: a figure the records do
not support is null (a median of nothing) or counted as unpriced, never guessed."""

import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime


def median_minutes(pairs: Iterable[tuple[datetime, datetime]]) -> float | None:
    """Median of (end - start) in minutes over (start, end) pairs; None without any pair.
    A pair whose end precedes its start (clock skew) counts as 0."""
    minutes = [max(0.0, (end - start).total_seconds() / 60) for start, end in pairs]
    return round(statistics.median(minutes), 1) if minutes else None


def share(part: int, total: int) -> float | None:
    """`part / total` rounded to 4 places; None when total is 0."""
    return round(part / total, 4) if total else None


def cost_avoided_paise(accepted: int, cheapest_supplier_unit_paise: int | None) -> int | None:
    """What buying the units a transfer delivered would have cost at the cheapest supplier
    price its match run recorded; None when that run recorded no supplier price."""
    if cheapest_supplier_unit_paise is None:
        return None
    return accepted * cheapest_supplier_unit_paise


@dataclass(frozen=True)
class HeldLot:
    """One batch a transfer's source request held: `held` units, and the quantity of the
    surplus post on that batch made before the request (None: the batch was not posted)."""

    held: int
    surplus_qty: int | None


def units_saved_from_expiry(accepted: int, lots: Sequence[HeldLot]) -> int:
    """Units of one received transfer that came from surplus-posted batches: per batch the
    smaller of what was held and what was posted, and in all no more than was accepted."""
    from_surplus = sum(min(lot.held, lot.surplus_qty) for lot in lots if lot.surplus_qty)
    return max(0, min(accepted, from_surplus))
