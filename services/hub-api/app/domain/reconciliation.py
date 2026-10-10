"""Receipt and reconciliation (business-rules.md §8 Shortage, §9). Pure, no I/O."""

from dataclasses import dataclass
from enum import StrEnum

from app.domain.shortage import Status


class Condition(StrEnum):
    GOOD = "GOOD"
    DAMAGED = "DAMAGED"
    TEMPERATURE_ISSUE = "TEMPERATURE_ISSUE"


class Outcome(StrEnum):
    """CONFIRMED: the shortage's shortfall was accepted in full. PARTIAL: less was accepted,
    and a residual shortage was opened for the rest."""

    CONFIRMED = "CONFIRMED"
    PARTIAL = "PARTIAL"


def receipt_problem(expected: int, received: int, accepted: int, rejected: int) -> str | None:
    """Why these receipt figures break §9's invariants, or None if they hold:
    received ≤ expected, and accepted + rejected = received."""
    if received > expected:
        return f"Received {received} is more than the {expected} expected."
    if accepted + rejected != received:
        return f"Accepted {accepted} plus rejected {rejected} must equal received {received}."
    return None


def inspection_note_missing(open_excursion: bool, note: str | None) -> bool:
    """§9: a shipment with any cold-chain EXCURSION on record (even one since RECOVERED)
    needs an inspection note."""
    return open_excursion and not (note or "").strip()


@dataclass(frozen=True)
class Result:
    outcome: Outcome
    status: Status  # RESOLVED or PARTIALLY_RESOLVED
    residual_qty: int  # the residual shortage's qty_required; 0 when none is opened


def reconcile(shortfall: int, accepted_total: int) -> Result:
    """§9, once every shipment of the shortage has a receipt. Accepted = shortfall:
    RESOLVED. Accepted < shortfall: PARTIALLY_RESOLVED with a residual of the difference.
    More than the shortfall cannot arrive (received ≤ expected, and the shipments carry the
    planned shortfall), so it is treated as covered rather than as a negative residual."""
    residual = max(0, shortfall - accepted_total)
    if residual == 0:
        return Result(Outcome.CONFIRMED, Status.RESOLVED, 0)
    return Result(Outcome.PARTIAL, Status.PARTIALLY_RESOLVED, residual)


# Factual SYSTEM reasons (§10).
ALL_RECEIVED = "Every shipment for this shortage has a receipt."
RESIDUAL_CREATED = "Residual shortage created."


def reconciled_reason(shortfall: int, accepted_total: int, residual: int) -> str:
    if residual == 0:
        return f"Accepted {accepted_total} of the {shortfall} short."
    return (
        f"Accepted {accepted_total} of the {shortfall} short; "
        f"a residual shortage of {residual} was opened."
    )


def residual_reason(parent_shortfall: int, accepted_total: int) -> str:
    return (
        f"Opened for the {parent_shortfall - accepted_total} not accepted "
        f"of the parent shortage's {parent_shortfall}."
    )


PO_RECEIVED = "The buyer recorded the receipt of this order's shipment."
