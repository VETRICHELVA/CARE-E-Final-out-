"""Resolution choice (business-rules.md §5).

1. A single hospital source covers the shortfall -> TRANSFER from the top-ranked one.
2. Else 2-3 hospital sources together cover it -> TRANSFER_SPLIT, greedy by rank.
3. Else -> BUY from the top-ranked supplier.
The best BUY is always kept as an alternative; for a BUY plan, the next supplier is.
Nothing eligible -> no plan.

CRITICAL TRANSFER (§7 step 2, S19): the plan also lists up to CRITICAL_PARALLEL_REQUESTS
top-ranked single-source candidates in `parallel` (the planned line first); each is asked at
once and the first to accept wins. Every other plan asks only its own lines."""

from collections.abc import Sequence
from dataclasses import dataclass

from app.domain import config
from app.domain.ranking import Option, rank

TRANSFER, TRANSFER_SPLIT, BUY = "TRANSFER", "TRANSFER_SPLIT", "BUY"


@dataclass(frozen=True)
class Line:
    option: Option
    qty: int
    landed_cost_paise: int


@dataclass(frozen=True)
class Plan:
    type: str
    lines: tuple[Line, ...]
    alternatives: tuple[Line, ...]
    parallel: tuple[Line, ...] = ()  # CRITICAL TRANSFER: the sources asked at once


def _line(option: Option, qty: int) -> Line:
    return Line(option, qty, option.landed_cost(qty))


def _greedy_split(ranked: Sequence[Option], need: int) -> tuple[Line, ...] | None:
    lines, left = [], need
    for option in ranked[: config.MAX_SPLIT_SOURCES]:
        qty = min(option.qty, left)
        lines.append(_line(option, qty))
        left -= qty
        if left == 0:
            return tuple(lines)
    return None


def plan(
    hospitals: Sequence[Option], suppliers: Sequence[Option], need: int, critical: bool
) -> Plan | None:
    """`hospitals`: sources passing every gate but quantity (any with qty 0 are skipped).
    `suppliers`: sources passing every gate, quantity included."""
    hospitals = [h for h in hospitals if h.qty > 0]
    buys = [_line(o, need) for o in rank(suppliers, need, critical)]
    singles = rank([h for h in hospitals if h.qty >= need], need, critical)
    if singles:
        parallel: tuple[Line, ...] = ()
        if critical:
            parallel = tuple(_line(o, need) for o in singles[: config.CRITICAL_PARALLEL_REQUESTS])
        return Plan(TRANSFER, (_line(singles[0], need),), tuple(buys[:1]), parallel)
    if split := _greedy_split(rank(hospitals, need, critical), need):
        return Plan(TRANSFER_SPLIT, split, tuple(buys[:1]))
    if buys:
        return Plan(BUY, tuple(buys[:1]), tuple(buys[1:2]))
    return None
