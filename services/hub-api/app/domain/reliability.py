"""Reliability and credits (business-rules.md §12, S19). Pure functions, no I/O.

    score = 40 × acceptance_rate + 25 × on_time_rate + 20 × (1 − discrepancy_rate)
            + 15 × response_speed
    response_speed = max(0, 1 − median(response minutes ÷ the §6 response limit))

Every input is a recorded outcome (CLAUDE.md rule 5): what a source answered and when, when
its shipments were recorded DELIVERED, and what the receiver accepted at reconciliation.

An org is scored from the components that apply to it (`answers_for`). Its answers are:
- a hospital's source requests. Of the requests it answered or let expire, acceptance_rate
  is the share it accepted. A request still waiting, or superseded before the source
  answered, is not counted. Response time runs from the request's creation to the answer.
- a supplier's purchase orders, since suppliers answer no source requests. Of the orders it
  answered (SENT → ACKNOWLEDGED or REJECTED), acceptance_rate = acknowledged ÷
  (acknowledged + rejected), where an order that ended REJECTED counts as rejected even if
  it was acknowledged first. An order still SENT is not counted. Response time runs from the
  order being SENT to the supplier's first answer.

For both, response_speed takes each answer's minutes ÷ the response limit of its shortage's
priority (§6), the median of those, then max(0, 1 − median); and
- on_time_rate: of the org's shipments recorded DELIVERED, the share delivered by the
  shortage's `required_by`.
- discrepancy_rate: Σ discrepancy ÷ Σ expected over the org's reconciled shipments.

The formula needs all four components. An org without the history for any of them scores
the no-history default (§5: 70). The score is rounded half up and clamped to 0-100."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from statistics import median

from app.domain import config

SUPPLIER = "SUPPLIER"  # OrgType.SUPPLIER: scored on its purchase orders

WEIGHTS = {"acceptance": 40, "on_time": 25, "accuracy": 20, "speed": 15}


@dataclass(frozen=True)
class Answer:
    """One answer: a source request answered or left to expire, or (`order_answers`) a
    supplier's answered purchase order."""

    accepted: bool
    response_minutes: float | None  # None: no answer was recorded (the deadline passed)
    sla_minutes: float  # the response limit of the shortage's priority (§6)


@dataclass(frozen=True)
class OrderAnswer:
    """One purchase order its supplier answered (§12, supplier)."""

    sent_at: datetime  # the order was created SENT
    answered_at: datetime  # the supplier's first answer: SENT → ACKNOWLEDGED or REJECTED
    rejected: bool  # the order ended REJECTED (from SENT, or after acknowledging)
    sla_minutes: float  # the response limit of the shortage's priority (§6)


def order_answers(orders: Sequence[OrderAnswer]) -> list[Answer]:
    """A supplier's purchase-order answers as §12 answers: acknowledged counts as accepted,
    rejected as not, and the response time is SENT → first answer."""
    return [
        Answer(
            accepted=not o.rejected,
            response_minutes=max(0.0, (o.answered_at - o.sent_at).total_seconds() / 60),
            sla_minutes=o.sla_minutes,
        )
        for o in orders
    ]


def answers_for(
    org_type: str, requests: Sequence[Answer], orders: Sequence[OrderAnswer]
) -> list[Answer]:
    """The answers that apply to the org: a supplier's purchase orders in place of source
    requests (which suppliers never answer), any other org's source requests. The hospital
    formula is unchanged."""
    return order_answers(orders) if org_type == SUPPLIER else list(requests)


@dataclass(frozen=True)
class History:
    answers: Sequence[Answer] = ()  # from `answers_for`
    on_time: Sequence[bool] = ()  # one per shipment recorded DELIVERED
    reconciled: Sequence[tuple[int, int]] = ()  # (expected, discrepancy) per shipment
    supplier: bool = False  # scored from the components that have history (§12)


@dataclass(frozen=True)
class Components:
    acceptance_rate: float | None
    median_response_minutes: float | None
    response_speed: float | None
    on_time_rate: float | None
    discrepancy_rate: float | None

    @property
    def complete(self) -> bool:
        """Every component of the formula has history behind it."""
        return None not in (
            self.acceptance_rate,
            self.response_speed,
            self.on_time_rate,
            self.discrepancy_rate,
        )


def components(history: History) -> Components:
    answers = list(history.answers)
    minutes = [a.response_minutes for a in answers if a.response_minutes is not None]
    ratios = [a.response_minutes / a.sla_minutes for a in answers if a.response_minutes is not None]
    expected = sum(e for e, _ in history.reconciled)
    on_time = list(history.on_time)
    return Components(
        acceptance_rate=sum(a.accepted for a in answers) / len(answers) if answers else None,
        median_response_minutes=float(median(minutes)) if minutes else None,
        response_speed=max(0.0, 1 - median(ratios)) if ratios else None,
        on_time_rate=sum(on_time) / len(on_time) if on_time else None,
        discrepancy_rate=(
            sum(d for _, d in history.reconciled) / expected if expected > 0 else None
        ),
    )


def formula(
    acceptance_rate: float, on_time_rate: float, discrepancy_rate: float, response_speed: float
) -> float:
    """§12, unrounded and unclamped."""
    return (
        WEIGHTS["acceptance"] * acceptance_rate
        + WEIGHTS["on_time"] * on_time_rate
        + WEIGHTS["accuracy"] * (1 - discrepancy_rate)
        + WEIGHTS["speed"] * response_speed
    )


def clamp(value: float) -> int:
    """Half up to a whole number, kept within 0-100."""
    return min(100, max(0, math.floor(value + 0.5)))


def score(c: Components, *, partial: bool = False) -> int:
    """The §12 score. A hospital needs history in all four components, else the no-history
    default. With `partial` (a supplier) the score is the weighted average of the components
    that have history, scaled to 0-100, and the default only while none has any."""
    parts = {
        "acceptance": c.acceptance_rate,
        "on_time": c.on_time_rate,
        "accuracy": None if c.discrepancy_rate is None else 1 - c.discrepancy_rate,
        "speed": c.response_speed,
    }
    known = {k: v for k, v in parts.items() if v is not None}
    if not known or (not partial and len(known) < len(parts)):
        return config.DEFAULT_RELIABILITY
    weight = sum(WEIGHTS[k] for k in known)
    return clamp(100 * sum(WEIGHTS[k] * v for k, v in known.items()) / weight)


def credits_for(accepted_units: int) -> int:
    """§12: +1 per UNITS_PER_CREDIT units transferred and reconciled (whole credits only)."""
    return max(0, accepted_units) // config.UNITS_PER_CREDIT


def credit_reason(accepted_units: int) -> str:
    """The ledger row's factual cause: only the figure the receiver recorded."""
    unit = "unit" if accepted_units == 1 else "units"
    return f"Transfer reconciled: {accepted_units:,} {unit} accepted by the receiver."
