"""Reliability and credits (business-rules.md §12, S19). Pure functions, no I/O.

    score = 40 × acceptance_rate + 25 × on_time_rate + 20 × (1 − discrepancy_rate)
            + 15 × response_speed
    response_speed = max(0, 1 − median_response_minutes ÷ SLA minutes)

Every input is a recorded outcome (CLAUDE.md rule 5): what a source answered and when, when
its shipments were recorded DELIVERED, and what the receiver accepted at reconciliation.

- acceptance_rate: of the requests the source answered or let expire, the share it accepted.
  A request still waiting, or superseded before the source answered, is not counted.
- response_speed: each answer's minutes ÷ the response limit of its shortage's priority (§6),
  the median of those, then max(0, 1 − median). With one priority this is exactly the formula.
- on_time_rate: of the source's shipments recorded DELIVERED, the share delivered by the
  shortage's `required_by`.
- discrepancy_rate: Σ discrepancy ÷ Σ expected over the source's reconciled shipments.

The formula needs all four components. An org without the history for any of them scores
the no-history default (§5: 70). The score is rounded half up and clamped to 0-100."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median

from app.domain import config

WEIGHTS = {"acceptance": 40, "on_time": 25, "accuracy": 20, "speed": 15}


@dataclass(frozen=True)
class Answer:
    """One request a source answered or let expire."""

    accepted: bool
    response_minutes: float | None  # None: no answer was recorded (the deadline passed)
    sla_minutes: float  # the response limit of the shortage's priority (§6)


@dataclass(frozen=True)
class History:
    answers: Sequence[Answer] = ()
    on_time: Sequence[bool] = ()  # one per shipment recorded DELIVERED
    reconciled: Sequence[tuple[int, int]] = ()  # (expected, discrepancy) per shipment


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


def score(c: Components) -> int:
    """The §12 score, or the no-history default while any component has no history."""
    if (
        c.acceptance_rate is None
        or c.on_time_rate is None
        or c.discrepancy_rate is None
        or c.response_speed is None
    ):
        return config.DEFAULT_RELIABILITY
    return clamp(formula(c.acceptance_rate, c.on_time_rate, c.discrepancy_rate, c.response_speed))


def credits_for(accepted_units: int) -> int:
    """§12: +1 per UNITS_PER_CREDIT units transferred and reconciled (whole credits only)."""
    return max(0, accepted_units) // config.UNITS_PER_CREDIT


def credit_reason(accepted_units: int) -> str:
    """The ledger row's factual cause: only the figure the receiver recorded."""
    unit = "unit" if accepted_units == 1 else "units"
    return f"Transfer reconciled: {accepted_units:,} {unit} accepted by the receiver."
