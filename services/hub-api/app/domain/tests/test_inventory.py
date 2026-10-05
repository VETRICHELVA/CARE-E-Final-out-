from dataclasses import dataclass, replace
from datetime import date, timedelta

import pytest

from app.domain.inventory import batch_transferable, days_to_expiry_at, is_expired

TODAY = date(2026, 10, 5)


@dataclass
class B:
    on_hand: int
    reserved: int = 0
    allocated: int = 0
    safety_stock: int = 0
    quarantined: int = 0
    expiry_date: date = TODAY + timedelta(days=180)


# Scenario 1 (demo-scenarios.md), Surgical Kit A
HOSPITAL_B = B(on_hand=2500, reserved=800, allocated=200, safety_stock=500)
HOSPITAL_C = B(on_hand=1400, reserved=800, safety_stock=500, expiry_date=TODAY + timedelta(200))


def test_hospital_b_has_1000_transferable() -> None:
    assert batch_transferable(HOSPITAL_B, TODAY) == 1000


def test_hospital_c_has_100_transferable() -> None:
    assert batch_transferable(HOSPITAL_C, TODAY) == 100


@pytest.mark.parametrize("term", ["reserved", "allocated", "safety_stock", "quarantined"])
def test_each_term_is_subtracted(term: str) -> None:
    bumped = replace(HOSPITAL_B, **{term: getattr(HOSPITAL_B, term) + 1})
    assert batch_transferable(bumped, TODAY) == 999


def test_on_hand_is_added() -> None:
    assert batch_transferable(replace(HOSPITAL_B, on_hand=2501), TODAY) == 1001


def test_held_qty_reduces_the_result() -> None:
    assert batch_transferable(HOSPITAL_B, TODAY, held_qty=300) == 700


def test_never_negative() -> None:
    assert batch_transferable(B(on_hand=100, reserved=80, safety_stock=50), TODAY) == 0
    assert batch_transferable(HOSPITAL_B, TODAY, held_qty=5000) == 0


def test_negative_held_qty_is_refused() -> None:
    with pytest.raises(ValueError):
        batch_transferable(HOSPITAL_B, TODAY, held_qty=-1)


@pytest.mark.parametrize(("days", "expired"), [(-1, True), (0, True), (1, False)])
def test_expiry_today_or_earlier_is_expired(days: int, expired: bool) -> None:
    batch = replace(HOSPITAL_B, expiry_date=TODAY + timedelta(days))
    assert is_expired(batch, TODAY) is expired
    assert batch_transferable(batch, TODAY) == (0 if expired else 1000)


@pytest.mark.parametrize(("days", "expected"), [(12, 12), (0, 0), (-3, -3)])
def test_days_to_expiry_at_arrival(days: int, expected: int) -> None:
    # Hospital D in Scenario 1: expiry +12 days -> "Expires in 12 days"
    batch = replace(HOSPITAL_B, expiry_date=TODAY + timedelta(days))
    assert days_to_expiry_at(batch, TODAY) == expected


def test_days_to_expiry_counts_from_arrival_not_today() -> None:
    assert days_to_expiry_at(HOSPITAL_B, TODAY + timedelta(days=3)) == 177
