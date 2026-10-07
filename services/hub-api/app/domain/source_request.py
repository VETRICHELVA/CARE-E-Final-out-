"""Source requests and holds (business-rules.md §2, §6, §7 steps 2-3 and 6, §8).
Pure functions, no I/O."""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from app.domain import config


class RequestStatus(StrEnum):
    REQUESTED = "REQUESTED"
    TENTATIVE_HOLD = "TENTATIVE_HOLD"
    DECLINED = "DECLINED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"
    CONFIRMED = "CONFIRMED"


class HoldStatus(StrEnum):
    TENTATIVE = "TENTATIVE"
    FIRM = "FIRM"
    RELEASED = "RELEASED"


R, H = RequestStatus, HoldStatus
# §8 SourceRequest. Any transition not listed is a 409 `invalid_transition`.
REQUEST_TRANSITIONS: dict[str, set[str]] = {
    R.REQUESTED: {R.TENTATIVE_HOLD, R.DECLINED, R.EXPIRED, R.SUPERSEDED},
    R.TENTATIVE_HOLD: {R.CONFIRMED, R.EXPIRED, R.SUPERSEDED},
}
# A tentative hold becomes FIRM on approval (S09) or is released; a FIRM hold is drawn
# down at pickup (S11).
HOLD_TRANSITIONS: dict[str, set[str]] = {
    H.TENTATIVE: {H.FIRM, H.RELEASED},
    H.FIRM: {H.RELEASED},
}
OPEN_REQUEST = frozenset({R.REQUESTED, R.TENTATIVE_HOLD})
ACTIVE_HOLD = frozenset({H.TENTATIVE, H.FIRM})  # counted as `reserved` (§2)

# Factual SYSTEM reasons (§10).
RESPONSE_DEADLINE_PASSED = "Response deadline passed."
HOLD_DEADLINE_PASSED = "Hold deadline passed."
STOCK_CHANGED = "Stock changed before acceptance."


def response_deadline(priority: str, now: datetime) -> datetime:
    """When a REQUESTED source must have accepted or declined (§6)."""
    return now + config.SOURCE_RESPONSE_LIMIT[priority]


def hold_deadline(priority: str, now: datetime) -> datetime:
    """When a TENTATIVE hold placed now expires unless the requester decides (§6)."""
    return now + config.TENTATIVE_HOLD_LIMIT[priority]


def is_overdue(deadline: datetime, now: datetime) -> bool:
    return deadline <= now


@dataclass(frozen=True)
class Lot:
    """One of the source's batches as accept sees it, after the §2 rules."""

    batch_id: uuid.UUID
    expiry_date: date
    transferable: int  # already net of every active hold


def allocate(lots: Iterable[Lot], qty: int) -> list[tuple[uuid.UUID, int]] | None:
    """Hold `qty` from the lots, earliest expiry first (ties by batch id).
    None if the lots together cannot cover it."""
    if qty <= 0:
        raise ValueError("qty must be positive.")
    picks, left = [], qty
    for lot in sorted(lots, key=lambda x: (x.expiry_date, x.batch_id)):
        if lot.transferable <= 0:
            continue
        take = min(lot.transferable, left)
        picks.append((lot.batch_id, take))
        left -= take
        if left == 0:
            return picks
    return None


def all_ready(statuses: Sequence[str]) -> bool:
    """§7 step 4: every request of the plan holds stock."""
    return bool(statuses) and all(s == R.TENTATIVE_HOLD for s in statuses)
