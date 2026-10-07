"""Shortage endpoints: create, list, read, cancel, manual re-run and the latest match run."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.catalog.models import Product
from app.conftest import ClientFor, World
from app.domain.shortage import Status
from app.orgs.models import Facility, Organization
from app.shortages.models import Shortage

pytestmark = pytest.mark.anyio


async def facility_of(session: AsyncSession, org: Organization) -> Facility:
    facility = await session.scalar(select(Facility).where(Facility.org_id == org.id))
    assert facility is not None
    return facility


def body(facility: Facility, product: Product, **overrides: Any) -> dict[str, Any]:
    """Scenario 1, step 1: required 1,000, local usable 150, CRITICAL, by now + 72 h."""
    return {
        "facility_id": str(facility.id),
        "product_id": str(product.id),
        "qty_required": 1000,
        "qty_local_usable": 150,
        "required_by": (datetime.now(UTC) + timedelta(hours=72)).isoformat(),
        "priority": "CRITICAL",
        "min_shelf_life_days": 30,
        **overrides,
    }


@pytest.fixture
async def requester_a(client_for: ClientFor, world: World) -> httpx.AsyncClient:
    return await client_for(world.users["a.REQUESTER"])


@pytest.fixture
async def manager_b(client_for: ClientFor, world: World) -> httpx.AsyncClient:
    return await client_for(world.users["b.STORE_MANAGER"])


@pytest.fixture
async def shortage_a(
    requester_a: httpx.AsyncClient,
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
) -> dict[str, Any]:
    facility = await facility_of(session, world.hospital_a)
    r = await requester_a.post("/shortages", json=body(facility, products["SURG-KIT-A"]))
    assert r.status_code == 201, r.text
    created: dict[str, Any] = r.json()
    return created


async def audit_actions(session: AsyncSession, entity_id: str) -> list[AuditLog]:
    stmt = select(AuditLog).where(AuditLog.entity_id == uuid.UUID(entity_id))
    return list(await session.scalars(stmt.order_by(AuditLog.ts)))


async def set_status(session: AsyncSession, shortage_id: str, status: Status) -> None:
    shortage = await session.get(Shortage, uuid.UUID(shortage_id))
    assert shortage is not None
    shortage.status = status
    await session.flush()


# --- create ----------------------------------------------------------------------------------


async def test_create_computes_the_shortfall_and_ignores_a_client_value(
    requester_a: httpx.AsyncClient,
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
) -> None:
    facility = await facility_of(session, world.hospital_a)
    r = await requester_a.post(
        "/shortages", json=body(facility, products["SURG-KIT-A"], shortfall=1)
    )
    assert r.status_code == 201, r.text
    out = r.json()
    assert (out["shortfall"], out["status"], out["source"]) == (850, "MATCHING", "FORM")
    assert out["org_id"] == str(world.hospital_a.id)
    stored = await session.get(Shortage, uuid.UUID(out["id"]))
    assert stored is not None and stored.shortfall == 850


async def test_shortfall_is_never_negative(
    requester_a: httpx.AsyncClient,
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
) -> None:
    facility = await facility_of(session, world.hospital_a)
    payload = body(facility, products["SURG-KIT-A"], qty_required=100, qty_local_usable=150)
    r = await requester_a.post("/shortages", json=payload)
    assert (r.status_code, r.json()["shortfall"]) == (201, 0)


async def test_min_shelf_life_defaults_to_the_product(
    requester_a: httpx.AsyncClient,
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
) -> None:
    facility = await facility_of(session, world.hospital_a)
    payload = body(facility, products["DIAG-RDK"])
    del payload["min_shelf_life_days"]
    r = await requester_a.post("/shortages", json=payload)
    assert (r.status_code, r.json()["min_shelf_life_days"]) == (201, 60)


async def test_create_audits_the_reason_as_user(
    requester_a: httpx.AsyncClient,
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
) -> None:
    facility = await facility_of(session, world.hospital_a)
    payload = body(facility, products["SURG-KIT-A"], reason="Theatre list doubled")
    r = await requester_a.post("/shortages", json=payload)
    rows = await audit_actions(session, r.json()["id"])
    assert [(x.action, x.reason_source) for x in rows] == [
        ("shortage.created", "USER"),
        ("shortage.status_changed", "SYSTEM"),
    ]
    assert rows[0].reason == "Theatre list doubled"
    assert rows[0].after is not None and rows[0].after["shortfall"] == 850


@pytest.mark.parametrize("field", [{"qty_requird": 5}, {"required_by": "2026-10-09T10:00:00"}])
async def test_create_rejects_unknown_fields_and_naive_times(
    requester_a: httpx.AsyncClient,
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
    field: dict[str, Any],
) -> None:
    facility = await facility_of(session, world.hospital_a)
    r = await requester_a.post("/shortages", json=body(facility, products["SURG-KIT-A"], **field))
    assert (r.status_code, r.json()["code"]) == (422, "schema_error")


async def test_create_needs_shortage_create(
    client_for: ClientFor, session: AsyncSession, world: World, products: dict[str, Product]
) -> None:
    receiver = await client_for(world.users["a.RECEIVER"])
    facility = await facility_of(session, world.hospital_a)
    r = await receiver.post("/shortages", json=body(facility, products["SURG-KIT-A"]))
    assert (r.status_code, r.json()["code"]) == (403, "forbidden")


async def test_create_for_another_orgs_facility_is_403(
    requester_a: httpx.AsyncClient,
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
) -> None:
    facility_b = await facility_of(session, world.hospital_b)
    r = await requester_a.post("/shortages", json=body(facility_b, products["SURG-KIT-A"]))
    assert (r.status_code, r.json()["code"]) == (403, "forbidden")


async def test_create_with_an_unknown_product_is_400(
    requester_a: httpx.AsyncClient, session: AsyncSession, world: World
) -> None:
    facility = await facility_of(session, world.hospital_a)
    payload = body(facility, products_stub := Product(id=uuid.uuid4()))
    assert products_stub.id is not None
    r = await requester_a.post("/shortages", json=payload)
    assert (r.status_code, r.json()["code"]) == (400, "validation")


# --- read ------------------------------------------------------------------------------------


async def test_list_shows_only_the_callers_org(
    requester_a: httpx.AsyncClient,
    manager_b: httpx.AsyncClient,
    shortage_a: dict[str, Any],
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
) -> None:
    facility_b = await facility_of(session, world.hospital_b)
    r = await manager_b.post("/shortages", json=body(facility_b, products["SURG-KIT-A"]))
    assert r.status_code == 201
    listed_a = (await requester_a.get("/shortages")).json()["items"]
    listed_b = (await manager_b.get("/shortages")).json()["items"]
    assert [s["id"] for s in listed_a] == [shortage_a["id"]]
    assert [s["id"] for s in listed_b] == [r.json()["id"]]


async def test_get_own_shortage(requester_a: httpx.AsyncClient, shortage_a: dict[str, Any]) -> None:
    r = await requester_a.get(f"/shortages/{shortage_a['id']}")
    assert (r.status_code, r.json()["shortfall"]) == (200, 850)


async def test_hospital_b_cannot_read_hospital_as_shortage(
    manager_b: httpx.AsyncClient, shortage_a: dict[str, Any]
) -> None:
    sid = shortage_a["id"]
    for r in (
        await manager_b.get(f"/shortages/{sid}"),
        await manager_b.get(f"/shortages/{sid}/match-runs/latest"),
    ):
        assert (r.status_code, r.json()["code"]) == (403, "forbidden")


async def test_unknown_shortage_is_404(requester_a: httpx.AsyncClient) -> None:
    r = await requester_a.get(f"/shortages/{uuid.uuid4()}")
    assert (r.status_code, r.json()["code"]) == (404, "not_found")


async def test_latest_match_run_for_any_user_of_the_requesters_org(
    client_for: ClientFor, world: World, shortage_a: dict[str, Any]
) -> None:
    approver = await client_for(world.users["a.APPROVER"])  # no shortage.create
    r = await approver.get(f"/shortages/{shortage_a['id']}/match-runs/latest")
    assert r.status_code == 200, r.text
    run = r.json()
    assert (run["run_no"], run["triggered_by"], run["candidates"]) == (1, "CREATE", [])
    assert (run["planned_resolution"], run["reason"]) == (None, "No eligible source")


# --- cancel ----------------------------------------------------------------------------------


async def test_cancel_from_matching(
    requester_a: httpx.AsyncClient, shortage_a: dict[str, Any], session: AsyncSession
) -> None:
    sid = shortage_a["id"]
    r = await requester_a.post(f"/shortages/{sid}/cancel", json={"reason": "Stock arrived"})
    assert (r.status_code, r.json()["status"]) == (200, "CANCELLED")
    row = (await audit_actions(session, sid))[-1]
    assert (row.action, row.before, row.after) == (
        "shortage.status_changed",
        {"status": "MATCHING"},
        {"status": "CANCELLED"},
    )
    assert (row.reason, row.reason_source) == ("Stock arrived", "USER")
    again = await requester_a.post(f"/shortages/{sid}/cancel")
    assert again.status_code == 409
    assert again.json() == {
        "code": "invalid_transition",
        "message": "Cannot move from CANCELLED to CANCELLED.",
        "details": {"from": "CANCELLED", "to": "CANCELLED"},
    }


@pytest.mark.parametrize(
    ("status", "allowed"),
    [
        (Status.OPEN, True),
        (Status.AWAITING_DECISION, True),
        (Status.DRAFT, False),
        (Status.IN_FULFILLMENT, False),
        (Status.RECEIVED, False),
        (Status.RESOLVED, False),
        (Status.PARTIALLY_RESOLVED, False),
    ],
)
async def test_cancel_only_from_open_matching_or_awaiting_decision(
    requester_a: httpx.AsyncClient,
    shortage_a: dict[str, Any],
    session: AsyncSession,
    status: Status,
    allowed: bool,
) -> None:
    await set_status(session, shortage_a["id"], status)
    r = await requester_a.post(f"/shortages/{shortage_a['id']}/cancel")
    if allowed:
        assert (r.status_code, r.json()["status"]) == (200, "CANCELLED")
    else:
        assert (r.status_code, r.json()["code"]) == (409, "invalid_transition")


async def test_cancel_without_a_reason_is_system(
    requester_a: httpx.AsyncClient, shortage_a: dict[str, Any], session: AsyncSession
) -> None:
    await requester_a.post(f"/shortages/{shortage_a['id']}/cancel")
    row = (await audit_actions(session, shortage_a["id"]))[-1]
    assert (row.reason, row.reason_source) == ("No reason was entered.", "SYSTEM")


async def test_another_org_cannot_cancel(
    manager_b: httpx.AsyncClient, shortage_a: dict[str, Any]
) -> None:
    r = await manager_b.post(f"/shortages/{shortage_a['id']}/cancel")
    assert (r.status_code, r.json()["code"]) == (403, "forbidden")


# --- manual re-run ---------------------------------------------------------------------------


async def test_manual_rerun(
    requester_a: httpx.AsyncClient,
    shortage_a: dict[str, Any],
    session: AsyncSession,
    world: World,
) -> None:
    r = await requester_a.post(f"/shortages/{shortage_a['id']}/match", json={"reason": "New stock"})
    assert r.status_code == 200, r.text
    run = r.json()
    assert (run["run_no"], run["triggered_by"]) == (2, "MANUAL")
    [row] = await audit_actions(session, run["id"])
    assert (row.action, row.actor_id, row.reason, row.reason_source) == (
        "match_run.created",
        world.users["a.REQUESTER"].id,
        "New stock",
        "USER",
    )
    latest = await requester_a.get(f"/shortages/{shortage_a['id']}/match-runs/latest")
    assert latest.json()["id"] == run["id"]


async def test_manual_rerun_from_open_starts_matching(
    requester_a: httpx.AsyncClient, shortage_a: dict[str, Any], session: AsyncSession
) -> None:
    await set_status(session, shortage_a["id"], Status.OPEN)
    r = await requester_a.post(f"/shortages/{shortage_a['id']}/match")
    assert r.status_code == 200
    got = await requester_a.get(f"/shortages/{shortage_a['id']}")
    assert got.json()["status"] == "MATCHING"


@pytest.mark.parametrize(
    "status", [Status.CANCELLED, Status.AWAITING_DECISION, Status.IN_FULFILLMENT, Status.RESOLVED]
)
async def test_manual_rerun_outside_open_or_matching_is_409(
    requester_a: httpx.AsyncClient,
    shortage_a: dict[str, Any],
    session: AsyncSession,
    status: Status,
) -> None:
    await set_status(session, shortage_a["id"], status)
    r = await requester_a.post(f"/shortages/{shortage_a['id']}/match")
    assert (r.status_code, r.json()["code"]) == (409, "invalid_transition")


async def test_another_org_cannot_rerun(
    manager_b: httpx.AsyncClient, shortage_a: dict[str, Any]
) -> None:
    r = await manager_b.post(f"/shortages/{shortage_a['id']}/match")
    assert (r.status_code, r.json()["code"]) == (403, "forbidden")
