"""Shortage rules: the shortfall (business-rules.md §1) and its state machine (§8)."""

from enum import StrEnum


class Status(StrEnum):
    DRAFT = "DRAFT"
    OPEN = "OPEN"
    MATCHING = "MATCHING"
    AWAITING_DECISION = "AWAITING_DECISION"
    IN_FULFILLMENT = "IN_FULFILLMENT"
    RECEIVED = "RECEIVED"
    RESOLVED = "RESOLVED"
    PARTIALLY_RESOLVED = "PARTIALLY_RESOLVED"
    CANCELLED = "CANCELLED"


S = Status
# Any transition not listed is a 409 `invalid_transition`.
TRANSITIONS: dict[str, set[str]] = {
    S.DRAFT: {S.OPEN},
    S.OPEN: {S.MATCHING, S.CANCELLED},
    S.MATCHING: {S.AWAITING_DECISION, S.CANCELLED},
    S.AWAITING_DECISION: {S.MATCHING, S.IN_FULFILLMENT, S.CANCELLED},
    S.IN_FULFILLMENT: {S.MATCHING, S.RECEIVED},
    S.RECEIVED: {S.RESOLVED, S.PARTIALLY_RESOLVED},
}


def shortfall(qty_required: int, qty_local_usable: int) -> int:
    """max(0, qty_required - qty_local_usable); computed by the hub, never taken from a client."""
    return max(0, qty_required - qty_local_usable)
