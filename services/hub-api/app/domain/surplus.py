"""Surplus posts (S18): the state machine, what a post offers, and the expiry band other orgs
see. Pure functions, no I/O."""

from datetime import date
from enum import StrEnum

from app.domain import config


class SurplusStatus(StrEnum):
    OPEN = "OPEN"
    MATCHED = "MATCHED"
    WITHDRAWN = "WITHDRAWN"
    EXPIRED = "EXPIRED"


class MatchKind(StrEnum):
    SHORTAGE = "SHORTAGE"  # another org's open shortage of the product
    FORECAST = "FORECAST"  # another org's forecast stock-out within 14 days


S = SurplusStatus
# Any transition not listed is a 409 `invalid_transition`.
TRANSITIONS: dict[str, set[str]] = {
    S.OPEN: {S.MATCHED, S.WITHDRAWN, S.EXPIRED},
    S.MATCHED: {S.WITHDRAWN, S.EXPIRED},
}
# Posts still on offer: their batch may not get a second live post.
LIVE = (S.OPEN, S.MATCHED)


def offered_qty(post_qty: int, transferable: int) -> int:
    """A post offers its qty only up to the batch's current transferable (CLAUDE.md rule 4)."""
    return max(0, min(post_qty, transferable))


def expiry_band(expiry_date: date, today: date) -> str:
    """The band other orgs see instead of the expiry date: "UNDER_30_DAYS", "30_TO_59_DAYS",
    "60_TO_89_DAYS" or "90_DAYS_OR_MORE" (with the default edges)."""
    days = (expiry_date - today).days
    edges = config.SURPLUS_EXPIRY_BAND_DAYS
    if days < edges[0]:
        return f"UNDER_{edges[0]}_DAYS"
    for low, high in zip(edges, edges[1:], strict=False):
        if days < high:
            return f"{low}_TO_{high - 1}_DAYS"
    return f"{edges[-1]}_DAYS_OR_MORE"
