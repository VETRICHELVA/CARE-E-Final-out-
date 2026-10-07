import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.domain import config, gates
from app.domain.gates import PASS, GateResult

NOW = datetime(2026, 10, 6, 6, 0, tzinfo=UTC)
P1, P2 = uuid.uuid4(), uuid.uuid4()


def fail(reason: str) -> GateResult:
    return GateResult(False, reason)


def test_product() -> None:
    assert gates.product(P1, P1) == PASS
    assert gates.product(P1, P2) == fail("Different product specification")


def test_quantity_single_source() -> None:
    assert gates.quantity(1000, 850) == PASS
    assert gates.quantity(850, 850) == PASS
    assert gates.quantity(100, 850) == fail("Only 100 transferable; 850 needed")


def test_quantity_split_candidate_needs_only_some_stock() -> None:
    assert gates.quantity(100, 850, split=True) == PASS
    assert gates.quantity(0, 850, split=True) == fail("Only 0 transferable; 850 needed")


def test_quantity_supplier_wording() -> None:
    assert gates.quantity(300, 850, noun="available") == fail("Only 300 available; 850 needed")


def test_shelf_life() -> None:
    assert gates.shelf_life(30, 30) == PASS
    assert gates.shelf_life(12, 30) == fail("Expires in 12 days; 30 required")
    assert gates.shelf_life(1, 30) == fail("Expires in 1 day; 30 required")
    assert gates.shelf_life(0, 30) == fail("Expires before delivery; 30 required")


def test_authorization() -> None:
    assert gates.authorization(org_active=True, authorized=True) == PASS
    assert gates.authorization(True, False) == fail("Not authorized to supply this product")
    assert gates.authorization(False, True) == fail("Organization is suspended")


@pytest.mark.parametrize(
    ("age", "max_age", "reason"),
    [
        (timedelta(hours=24), config.VERIFIED_WITHIN_CRITICAL, None),
        (timedelta(hours=26), config.VERIFIED_WITHIN_CRITICAL, "Stock last verified 26 h ago"),
        (timedelta(days=7), config.VERIFIED_WITHIN_ROUTINE, None),
        (
            timedelta(days=9, hours=3),
            config.VERIFIED_WITHIN_ROUTINE,
            "Stock last verified 9 days ago",
        ),
    ],
)
def test_freshness(age: timedelta, max_age: timedelta, reason: str | None) -> None:
    expected = PASS if reason is None else fail(reason)
    assert gates.freshness(NOW - age, NOW, max_age) == expected


def test_freshness_never_verified_and_offer_wording() -> None:
    assert gates.freshness(None, NOW, config.VERIFIED_WITHIN_ROUTINE) == fail(
        "Stock has never been verified"
    )
    stale = gates.freshness(
        NOW - timedelta(days=8), NOW, config.OFFER_UPDATED_WITHIN, what="Offer last updated"
    )
    assert stale == fail("Offer last updated 8 days ago")


def test_deadline() -> None:
    assert gates.deadline(NOW, NOW) == PASS
    assert gates.deadline(NOW + timedelta(hours=5, minutes=10), NOW) == fail(
        "Arrives 6 h after the deadline"
    )


def test_cold_chain() -> None:
    assert gates.cold_chain(requires=False, cold_storage=False, vehicle=False) == PASS
    assert gates.cold_chain(True, True, True) == PASS
    assert gates.cold_chain(True, False, True) == fail("No cold storage at the source facility")
    assert gates.cold_chain(True, True, False) == fail("No cold-chain transport available")
