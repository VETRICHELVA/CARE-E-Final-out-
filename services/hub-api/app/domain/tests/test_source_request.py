import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from app.domain import source_request as sr
from app.domain.source_request import (
    HOLD_TRANSITIONS,
    REQUEST_TRANSITIONS,
    HoldStatus,
    Lot,
    RequestStatus,
    allocate,
)
from app.domain.state_machine import InvalidTransition, transition

NOW = datetime(2026, 10, 6, 6, 0, tzinfo=UTC)
D = date(2026, 10, 6)


def ids(n: int) -> list[uuid.UUID]:
    return sorted(uuid.uuid4() for _ in range(n))


def test_deadlines_follow_the_time_limits() -> None:
    assert sr.response_deadline("CRITICAL", NOW) == NOW + timedelta(minutes=15)
    assert sr.response_deadline("ROUTINE", NOW) == NOW + timedelta(hours=4)
    assert sr.hold_deadline("CRITICAL", NOW) == NOW + timedelta(minutes=30)
    assert sr.hold_deadline("ROUTINE", NOW) == NOW + timedelta(hours=24)


def test_overdue_at_the_deadline() -> None:
    assert not sr.is_overdue(NOW, NOW - timedelta(seconds=1))
    assert sr.is_overdue(NOW, NOW)


def test_allocate_takes_earliest_expiry_first() -> None:
    a, b, c = ids(3)
    lots = [Lot(a, D + timedelta(days=200), 600), Lot(b, D + timedelta(days=60), 400)]
    assert allocate(lots, 850) == [(b, 400), (a, 450)]
    assert allocate(lots, 300) == [(b, 300)]
    assert allocate([*lots, Lot(c, D + timedelta(days=10), 0)], 1000) == [(b, 400), (a, 600)]


def test_allocate_breaks_expiry_ties_by_batch_id() -> None:
    a, b = ids(2)
    assert allocate([Lot(b, D, 5), Lot(a, D, 5)], 7) == [(a, 5), (b, 2)]


def test_allocate_returns_none_when_short() -> None:
    a, b = ids(2)
    assert allocate([Lot(a, D, 500), Lot(b, D, 349)], 850) is None
    assert allocate([], 1) is None
    with pytest.raises(ValueError):
        allocate([Lot(a, D, 5)], 0)


@pytest.mark.parametrize(
    ("src", "dst", "ok"),
    [
        ("REQUESTED", "TENTATIVE_HOLD", True),
        ("REQUESTED", "DECLINED", True),
        ("REQUESTED", "EXPIRED", True),
        ("REQUESTED", "SUPERSEDED", True),
        ("TENTATIVE_HOLD", "CONFIRMED", True),
        ("TENTATIVE_HOLD", "EXPIRED", True),
        ("TENTATIVE_HOLD", "SUPERSEDED", True),
        ("REQUESTED", "CONFIRMED", False),
        ("TENTATIVE_HOLD", "DECLINED", False),
        ("TENTATIVE_HOLD", "TENTATIVE_HOLD", False),
        ("DECLINED", "TENTATIVE_HOLD", False),
        ("EXPIRED", "TENTATIVE_HOLD", False),
        ("SUPERSEDED", "EXPIRED", False),
        ("CONFIRMED", "EXPIRED", False),
    ],
)
def test_request_state_machine(src: str, dst: str, ok: bool) -> None:
    class Req:
        status = src

    if ok:
        assert transition(Req(), dst, REQUEST_TRANSITIONS) == src
    else:
        with pytest.raises(InvalidTransition):
            transition(Req(), dst, REQUEST_TRANSITIONS)


def test_hold_state_machine() -> None:
    assert HOLD_TRANSITIONS[HoldStatus.TENTATIVE] == {HoldStatus.FIRM, HoldStatus.RELEASED}
    # S11: a FIRM hold is consumed at pickup (§9); only TENTATIVE holds become FIRM.
    assert HOLD_TRANSITIONS[HoldStatus.FIRM] == {HoldStatus.RELEASED, HoldStatus.CONSUMED}
    assert HoldStatus.RELEASED not in HOLD_TRANSITIONS
    assert HoldStatus.CONSUMED not in HOLD_TRANSITIONS
    assert {HoldStatus.TENTATIVE, HoldStatus.FIRM} == sr.ACTIVE_HOLD


def test_all_ready() -> None:
    held, asked = RequestStatus.TENTATIVE_HOLD, RequestStatus.REQUESTED
    assert sr.all_ready([held, held])
    assert not sr.all_ready([held, asked])
    assert not sr.all_ready([])
