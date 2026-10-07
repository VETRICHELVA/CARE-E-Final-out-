"""Recommendations (business-rules.md §5, §6, §7 steps 4-6, §8, §13). Pure functions, no I/O.

The explanation is built only from template text and the stored candidate data passed in;
no AI, and no figure that the caller did not hand over. A hospital source's cost is never an
input, so it cannot reach the requester through the text (CLAUDE.md rule 6)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.domain import config
from app.domain.resolution import BUY, TRANSFER, TRANSFER_SPLIT


class RecStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ESCALATED = "ESCALATED"
    EXPIRED = "EXPIRED"


R = RecStatus
# §8 Recommendation. Any transition not listed is a 409 `invalid_transition`.
REC_TRANSITIONS: dict[str, set[str]] = {
    R.PENDING: {R.APPROVED, R.REJECTED, R.ESCALATED, R.EXPIRED},
    R.ESCALATED: {R.APPROVED, R.REJECTED, R.EXPIRED},
}
OPEN_RECOMMENDATION = frozenset({R.PENDING, R.ESCALATED})  # still waiting for a decision

# Factual SYSTEM reasons (§10).
VALIDITY_PASSED = "Recommendation validity passed."
REJECTED = "The recommendation was rejected."
SHORTAGE_CANCELLED = "The shortage was cancelled."

# §13: what the requester sees after approving.
APPROVED_MESSAGE = {
    TRANSFER: "Stock is now held at the source.",
    TRANSFER_SPLIT: "Stock is now held at each source.",
    BUY: "The order has gone to the supplier.",
}


def valid_until(priority: str, now: datetime) -> datetime:
    """When a recommendation created now expires unless someone decides (§6)."""
    return now + config.RECOMMENDATION_VALIDITY[priority]


def is_expired(expires_at: datetime, now: datetime) -> bool:
    return expires_at <= now


def about_hours(eta_hours: float) -> int:
    """An ETA as the explanation states it: whole hours, half up, at least 1."""
    return max(1, int(eta_hours + 0.5))


def rupees(paise: int) -> str:
    """₹ with thousands separators and the paise, e.g. 2419050 -> "₹24,190.50"."""
    return f"₹{paise // 100:,}.{paise % 100:02d}"


def _plural(n: int, word: str) -> str:
    return word if n == 1 else f"{word}s"


def _units(n: int) -> str:
    return f"{n:,} {_plural(n, 'unit')}"


def _sources(n: int) -> str:
    return f"{n} {_plural(n, 'source')}"


@dataclass(frozen=True)
class Source:
    """A planned or alternative source as the explanation may describe it. `cost_paise` is a
    supplier's landed cost; hospital sources have none here (rule 6)."""

    name: str
    qty: int
    eta_hours: float
    shelf_life_days: int | None = None  # hospital: days left at delivery on the held stock
    cost_paise: int | None = None  # supplier only


@dataclass(frozen=True)
class Rejected:
    name: str
    reasons: tuple[str, ...]  # every failed gate's reason, in gate order


def _hospital(s: Source) -> str:
    days = ""
    if (n := s.shelf_life_days) is not None:
        days = f" with {n:,} {_plural(n, 'day')} of shelf life at delivery"
    eta = about_hours(s.eta_hours)
    return f"{s.name} holds {_units(s.qty)}{days} and can deliver in about {eta} h"


def _buy(s: Source) -> str:
    cost = f" for {rupees(s.cost_paise)}" if s.cost_paise is not None else ""
    return (
        f"buy {_units(s.qty)} from {s.name}{cost}, arriving in about {about_hours(s.eta_hours)} h"
    )


def explain(
    rec_type: str,
    lines: Sequence[Source],
    alternative: Source | None,
    *,
    shortfall: int,
    critical: bool,
    other_eligible: int,
    rejected: Sequence[Rejected],
    left_out: Sequence[tuple[str, str]] = (),
) -> str:
    """The deterministic explanation of a recommendation. It always names the BUY
    alternative (or says there is none). `left_out` is (name, why) for orgs excluded from
    this shortage's matching after an earlier request ended."""
    parts: list[str] = []
    if rec_type == TRANSFER:
        parts.append(f"{_hospital(lines[0])}.")
    elif rec_type == TRANSFER_SPLIT:
        parts.append(
            f"No single hospital source covers the shortfall of {_units(shortfall)}, so it is "
            f"split across {_sources(len(lines))}: " + "; ".join(_hospital(s) for s in lines) + "."
        )
    elif rec_type == BUY:
        others = config.MAX_SPLIT_SOURCES - 1
        (s,) = lines
        parts.append(
            f"No eligible hospital source, alone or with up to {others} others, covers the "
            f"shortfall of {_units(shortfall)}. Recommended: {_buy(s)}."
        )
    else:
        raise ValueError(f"Unknown recommendation type {rec_type}.")

    basis = "earliest arrival" if critical else "lowest landed cost"
    priority = "CRITICAL" if critical else "ROUTINE"
    parts.append(f"Sources are ranked by {basis} because the shortage is {priority}.")
    if other_eligible:
        noun = _plural(other_eligible, "source")
        parts.append(f"{other_eligible} other eligible {noun} ranked lower.")
    if rejected:
        listed = "; ".join(f"{r.name} ({', '.join(r.reasons)})" for r in rejected)
        n = len(rejected)
        verb = "was" if n == 1 else "were"
        parts.append(f"{n} other {_plural(n, 'source')} {verb} not eligible: {listed}.")
    if left_out:
        listed = "; ".join(f"{name} ({why})" for name, why in left_out)
        parts.append(f"Not asked again for this shortage: {listed}.")
    if alternative is not None:
        instead = " instead" if rec_type == BUY else ""
        parts.append(f"Alternative: {_buy(alternative)}{instead}.")
    elif rec_type == BUY:
        parts.append("Alternative: no other supplier is eligible.")
    else:
        parts.append("Alternative: no supplier is eligible to buy from.")
    return " ".join(parts)
