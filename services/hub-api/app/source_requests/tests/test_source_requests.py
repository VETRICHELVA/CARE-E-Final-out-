"""Source requests and holds: creation from a match run, accept with tentative holds,
decline with re-run, listing, holds counted as reserved, and cancel releasing holds."""

import uuid
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.catalog.models import Product
from app.conftest import ClientFor, World
from app.inventory.models import InventoryBatch
from app.orgs.models import OrgType
from app.shortages import service as shortages
from app.shortages.models import MatchRun, Priority, Shortage
from app.source_requests import service
from app.source_requests.models import Hold, SourceRequest
from app.source_requests.tests.conftest import (
    Orgs,
    add_batch,
    add_org,
    add_user,
    authorize,
    create_shortage,
    requests_of,
)

pytestmark = pytest.mark.anyio


async def runs_of(session: AsyncSession, shortage: Shortage) -> list[MatchRun]:
    stmt = select(MatchRun).where(MatchRun.shortage_id == shortage.id)
    return list(await session.scalars(stmt.order_by(MatchRun.run_no)))


async def audit_of(session: AsyncSession, entity_id: uuid.UUID) -> list[AuditLog]:
    stmt = select(AuditLog).where(AuditLog.entity_id == entity_id)
    return list(await session.scalars(stmt.order_by(AuditLog.ts)))


async def holds_of(session: AsyncSession, sr: SourceRequest) -> list[Hold]:
    stmt = select(Hold).where(Hold.source_request_id == sr.id)
    return list(await session.scalars(stmt.order_by(Hold.created_at, Hold.id)))


@pytest.fixture
async def shortage(
    session: AsyncSession, world: World, products: dict[str, Product], s1: Orgs, now: datetime
) -> Shortage:
    return await create_shortage(session, world.users["a.REQUESTER"], products["SURG-KIT-A"], now)


@pytest.fixture
async def request_b(session: AsyncSession, shortage: Shortage) -> SourceRequest:
    (sr,) = await requests_of(session, shortage)
    return sr


@pytest.fixture
async def manager_b(client_for: ClientFor, world: World) -> httpx.AsyncClient:
    return await client_for(world.users["b.STORE_MANAGER"])


# --- creation ----------------------------------------------------------------------------------


async def test_a_transfer_plan_sends_one_request_with_the_response_deadline(
    session: AsyncSession, world: World, shortage: Shortage, s1: Orgs, now: datetime
) -> None:
    """Scenario 1 step 2: TRANSFER from B, so B gets a request due in 15 minutes."""
    (sr,) = await requests_of(session, shortage)
    run = (await runs_of(session, shortage))[-1]
    assert (sr.source_org_id, sr.qty, sr.status) == (world.hospital_b.id, 850, "REQUESTED")
    assert sr.sla_deadline == now + timedelta(minutes=15)
    assert run.planned_resolution is not None
    assert str(sr.candidate_id) == run.planned_resolution["lines"][0]["candidate_id"]
    (row,) = await audit_of(session, sr.id)
    assert (row.action, row.actor_id, row.org_id, row.reason_source) == (
        "source_request.created",
        None,
        world.hospital_a.id,
        "SYSTEM",
    )
    assert row.reason == "Match run 1 planned a TRANSFER from this source."


async def test_a_routine_request_gets_four_hours(
    session: AsyncSession, world: World, products: dict[str, Product], s1: Orgs, now: datetime
) -> None:
    user, ska = world.users["a.REQUESTER"], products["SURG-KIT-A"]
    routine = await create_shortage(session, user, ska, now, priority=Priority.ROUTINE)
    (sr,) = await requests_of(session, routine)
    assert sr.sla_deadline == now + timedelta(hours=4)


async def test_a_split_plan_sends_one_request_per_source(
    session: AsyncSession, world: World, products: dict[str, Product], now: datetime
) -> None:
    ska = products["SURG-KIT-A"]
    p = await add_org(session, "Hospital P", OrgType.HOSPITAL, 12.95, 77.60)
    q = await add_org(session, "Hospital Q", OrgType.HOSPITAL, 12.96, 77.60)
    await add_batch(session, p, ska, now, on_hand=500, expiry_days=180)
    await add_batch(session, q, ska, now, on_hand=350, expiry_days=180)
    authorize(session, ska, p, q)
    split = await create_shortage(session, world.users["a.REQUESTER"], ska, now)
    got = sorted((sr.source_org_id, sr.qty) for sr in await requests_of(session, split))
    assert got == sorted([(p.id, 500), (q.id, 350)])


async def test_a_buy_plan_sends_no_request(
    session: AsyncSession, world: World, products: dict[str, Product], s1: Orgs, now: datetime
) -> None:
    # 2,000 short: no hospital (or pair of them) covers it, so the plan is BUY.
    user, ska = world.users["a.REQUESTER"], products["SURG-KIT-A"]
    buy = await create_shortage(session, user, ska, now, qty_required=2150)
    assert (await runs_of(session, buy))[-1].planned_resolution["type"] == "BUY"  # type: ignore[index]
    assert await requests_of(session, buy) == []


# --- accept ------------------------------------------------------------------------------------


async def test_accept_places_tentative_holds_earliest_expiry_first(
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
    shortage: Shortage,
    request_b: SourceRequest,
    manager_b: httpx.AsyncClient,
    now: datetime,
) -> None:
    ska, b = products["SURG-KIT-A"], world.hospital_b
    # Two more B batches: one expiring sooner than the 1,000-unit batch, one too soon to count.
    soon = await add_batch(session, b, ska, now, on_hand=300, expiry_days=60, batch_no="SOON")
    short = await add_batch(session, b, ska, now, on_hand=900, expiry_days=10, batch_no="SHORT")
    big = await session.scalar(
        select(InventoryBatch).where(
            InventoryBatch.batch_no == "B-1", InventoryBatch.org_id == b.id
        )
    )
    assert big is not None

    r = await manager_b.post(f"/source-requests/{request_b.id}/accept", json={"reason": "OK"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["status"], body["held_qty"], body["qty"]) == ("TENTATIVE_HOLD", 850, 850)
    assert body["responded_by"] == str(world.users["b.STORE_MANAGER"].id)
    hold_deadline = datetime.fromisoformat(body["hold_expires_at"])
    assert now + timedelta(minutes=30) <= hold_deadline <= now + timedelta(minutes=31)
    assert [(h["batch_id"], h["qty"], h["status"]) for h in body["holds"]] == [
        (str(soon.id), 300, "TENTATIVE"),
        (str(big.id), 550, "TENTATIVE"),
    ]
    assert short.id not in {h.batch_id for h in await holds_of(session, request_b)}

    rows = await audit_of(session, request_b.id)
    assert [(x.action, x.after) for x in rows][-1][0] == "source_request.status_changed"
    last = rows[-1]
    assert (last.before, last.after["status"], last.reason, last.reason_source) == (  # type: ignore[index]
        {"status": "REQUESTED"},
        "TENTATIVE_HOLD",
        "OK",
        "USER",
    )
    assert (last.actor_id, last.org_id) == (world.users["b.STORE_MANAGER"].id, world.hospital_a.id)
    for h in await holds_of(session, request_b):
        (created,) = await audit_of(session, h.id)
        assert (created.action, created.org_id) == ("hold.created", b.id)

    # B's inventory now shows the holds as reserved.
    listed = (await manager_b.get("/inventory/batches")).json()["items"]
    by_no = {x["batch_no"]: x for x in listed}
    assert (by_no["SOON"]["held_qty"], by_no["SOON"]["transferable"]) == (300, 0)
    assert (by_no["B-1"]["held_qty"], by_no["B-1"]["transferable"]) == (550, 450)


async def test_accept_twice_is_409(request_b: SourceRequest, manager_b: httpx.AsyncClient) -> None:
    assert (await manager_b.post(f"/source-requests/{request_b.id}/accept")).status_code == 200
    again = await manager_b.post(f"/source-requests/{request_b.id}/accept")
    assert again.status_code == 409
    assert again.json()["code"] == "invalid_transition"
    assert again.json()["details"] == {"from": "TENTATIVE_HOLD", "to": "TENTATIVE_HOLD"}
    decline = await manager_b.post(f"/source-requests/{request_b.id}/decline")
    assert (decline.status_code, decline.json()["code"]) == (409, "invalid_transition")


@pytest.mark.parametrize("action", ["accept", "decline"])
async def test_only_the_source_org_can_answer(
    client_for: ClientFor,
    world: World,
    request_b: SourceRequest,
    session: AsyncSession,
    action: str,
) -> None:
    # Hospital A's store manager can respond to requests, but this one is B's: 403.
    for user in (world.users["a.STORE_MANAGER"], world.users["s.SUPPLIER_DESK"]):
        r = await (await client_for(user)).post(f"/source-requests/{request_b.id}/{action}")
        assert (r.status_code, r.json()["code"]) == (403, "forbidden")
    # B's approver lacks `source_request.respond`.
    r = await (await client_for(world.users["b.APPROVER"])).post(
        f"/source-requests/{request_b.id}/{action}"
    )
    assert (r.status_code, r.json()["details"]) == (403, {"capability": "source_request.respond"})
    await session.refresh(request_b)
    assert request_b.status == "REQUESTED"
    missing = await (await client_for(world.users["b.STORE_MANAGER"])).post(
        f"/source-requests/{uuid.uuid4()}/{action}"
    )
    assert missing.status_code == 404


async def test_accept_when_stock_changed_expires_and_rematches(
    session: AsyncSession,
    world: World,
    shortage: Shortage,
    request_b: SourceRequest,
    manager_b: httpx.AsyncClient,
    s1: Orgs,
) -> None:
    batch = await session.scalar(
        select(InventoryBatch).where(InventoryBatch.org_id == world.hospital_b.id)
    )
    assert batch is not None
    batch.on_hand = 2000  # 500 transferable now; the request is for 850
    await session.flush()

    r = await manager_b.post(f"/source-requests/{request_b.id}/accept")
    assert r.status_code == 409
    assert r.json() == {
        "code": "conflict",
        "message": "Stock changed before acceptance.",
        "details": {"requested_qty": 850, "transferable_qty": 500},
    }
    await session.refresh(request_b)
    assert request_b.status == "EXPIRED"
    assert await holds_of(session, request_b) == []
    last = (await audit_of(session, request_b.id))[-1]
    assert (last.after, last.actor_id, last.reason, last.reason_source) == (
        {"status": "EXPIRED"},
        None,
        "Stock changed before acceptance.",
        "SYSTEM",
    )
    run = (await runs_of(session, shortage))[-1]
    assert (run.run_no, run.triggered_by, run.excluded_org_ids) == (2, "EXPIRY", [])
    # B's 500 is not enough on its own, so the plan falls back to BUY from Supplier Y.
    assert run.planned_resolution is not None
    assert run.planned_resolution["type"] == "BUY"
    assert run.planned_resolution["lines"][0]["source_org_id"] == str(s1["Supplier Y"].id)


async def test_an_answer_after_the_deadline_expires_the_request(
    session: AsyncSession,
    world: World,
    shortage: Shortage,
    request_b: SourceRequest,
    now: datetime,
) -> None:
    late = now + timedelta(minutes=15)
    with pytest.raises(service.Settled) as e:
        await service.accept(session, world.users["b.STORE_MANAGER"], request_b.id, None, now=late)
    assert (e.value.error.status, e.value.error.code) == (409, "invalid_transition")
    assert request_b.status == "EXPIRED"
    run = (await runs_of(session, shortage))[-1]
    assert (run.triggered_by, run.excluded_org_ids) == ("EXPIRY", [world.hospital_b.id])


# --- decline ------------------------------------------------------------------------------------


async def test_scenario_1_step_3_decline_without_reason_reruns_without_b(
    session: AsyncSession,
    world: World,
    shortage: Shortage,
    request_b: SourceRequest,
    manager_b: httpx.AsyncClient,
    s1: Orgs,
) -> None:
    r = await manager_b.post(f"/source-requests/{request_b.id}/decline")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["status"], body["decline_reason"], body["reason_source"]) == (
        "DECLINED",
        None,
        "SYSTEM",
    )
    row = (await audit_of(session, request_b.id))[-1]
    assert (row.before, row.after["status"], row.reason, row.reason_source) == (  # type: ignore[index]
        {"status": "REQUESTED"},
        "DECLINED",
        "No reason was entered.",
        "SYSTEM",
    )
    assert row.actor_id == world.users["b.STORE_MANAGER"].id
    # Step 4: C, D and E still fail, so the re-run (without B) buys from Y, X as alternative.
    run = (await runs_of(session, shortage))[-1]
    assert (run.run_no, run.triggered_by, run.excluded_org_ids) == (
        2,
        "DECLINE",
        [world.hospital_b.id],
    )
    plan = run.planned_resolution
    assert plan is not None
    assert plan["type"] == "BUY"
    assert [x["source_org_id"] for x in plan["lines"]] == [str(s1["Supplier Y"].id)]
    assert [x["source_org_id"] for x in plan["alternatives"]] == [str(s1["Supplier X"].id)]
    assert shortage.status == "MATCHING"
    assert len(await requests_of(session, shortage)) == 1  # a BUY asks no one


async def test_decline_with_a_reason_is_user(
    session: AsyncSession, request_b: SourceRequest, manager_b: httpx.AsyncClient
) -> None:
    r = await manager_b.post(
        f"/source-requests/{request_b.id}/decline", json={"reason": "  Needed for surgery  "}
    )
    assert (r.json()["decline_reason"], r.json()["reason_source"]) == (
        "Needed for surgery",
        "USER",
    )
    row = (await audit_of(session, request_b.id))[-1]
    assert (row.reason, row.reason_source) == ("Needed for surgery", "USER")
    again = await manager_b.post(f"/source-requests/{request_b.id}/decline")
    assert (again.status_code, again.json()["code"]) == (409, "invalid_transition")
    accept = await manager_b.post(f"/source-requests/{request_b.id}/accept")
    assert (accept.status_code, accept.json()["code"]) == (409, "invalid_transition")


async def test_a_split_decline_releases_the_other_sources_holds(
    session: AsyncSession, world: World, products: dict[str, Product], now: datetime
) -> None:
    ska = products["SURG-KIT-A"]
    p = await add_org(session, "Hospital P", OrgType.HOSPITAL, 12.95, 77.60)
    q = await add_org(session, "Hospital Q", OrgType.HOSPITAL, 12.96, 77.60)
    await add_batch(session, p, ska, now, on_hand=500, expiry_days=180)
    await add_batch(session, q, ska, now, on_hand=350, expiry_days=180)
    authorize(session, ska, p, q)
    p_user = await add_user(session, p, "STORE_MANAGER", "sm@p.test")
    q_user = await add_user(session, q, "STORE_MANAGER", "sm@q.test")
    split = await create_shortage(session, world.users["a.REQUESTER"], ska, now)
    by_org = {sr.source_org_id: sr for sr in await requests_of(session, split)}

    await service.accept(session, p_user, by_org[p.id].id, None, now=now)
    (p_hold,) = await holds_of(session, by_org[p.id])
    await service.decline(session, q_user, by_org[q.id].id, None, now=now)

    assert (by_org[p.id].status, p_hold.status) == ("SUPERSEDED", "RELEASED")
    superseded = (await audit_of(session, by_org[p.id].id))[-1]
    assert (superseded.reason, superseded.reason_source, superseded.actor_id) == (
        "Another source request for this shortage ended: The source declined the request.",
        "SYSTEM",
        None,
    )
    released = (await audit_of(session, p_hold.id))[-1]
    assert (released.action, released.after, released.org_id) == (
        "hold.status_changed",
        {"status": "RELEASED"},
        p.id,
    )
    run = (await runs_of(session, split))[-1]
    assert run.excluded_org_ids == [q.id]


# --- holds are reserved for everyone else --------------------------------------------------------


async def test_held_stock_is_reserved_for_other_shortages(
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
    s1: Orgs,
    request_b: SourceRequest,
    now: datetime,
) -> None:
    await service.accept(session, world.users["b.STORE_MANAGER"], request_b.id, None, now=now)
    c_user = await add_user(session, s1["Hospital C"], "REQUESTER", "req@c.test")
    other = await create_shortage(
        session, c_user, products["SURG-KIT-A"], now, qty_required=850, qty_local_usable=0
    )
    run = (await runs_of(session, other))[-1]
    out = await shortages.match_run_out(session, run)
    b = next(c for c in out.candidates if c.source_org_name == "Hospital B")
    assert b.transferable_qty == 150  # 1,000 - 850 held for Hospital A
    assert [g.reason for g in b.gate_results if not g.passed] == [
        "Only 150 transferable; 850 needed"
    ]


# --- cancel and manual re-run --------------------------------------------------------------------


async def test_cancel_releases_holds_and_supersedes_requests(
    session: AsyncSession,
    world: World,
    client_for: ClientFor,
    shortage: Shortage,
    request_b: SourceRequest,
    now: datetime,
) -> None:
    await service.accept(session, world.users["b.STORE_MANAGER"], request_b.id, None, now=now)
    requester = await client_for(world.users["a.REQUESTER"])
    r = await requester.post(f"/shortages/{shortage.id}/cancel", json={"reason": "Found stock"})
    assert (r.status_code, r.json()["status"]) == (200, "CANCELLED")
    await session.refresh(request_b)
    assert request_b.status == "SUPERSEDED"
    assert {h.status for h in await holds_of(session, request_b)} == {"RELEASED"}
    row = (await audit_of(session, request_b.id))[-1]
    assert (row.after, row.reason, row.reason_source, row.actor_id) == (
        {"status": "SUPERSEDED"},
        "Found stock",
        "USER",
        world.users["a.REQUESTER"].id,
    )


async def test_manual_rerun_while_requests_are_open_is_409(
    client_for: ClientFor, world: World, shortage: Shortage, request_b: SourceRequest
) -> None:
    requester = await client_for(world.users["a.REQUESTER"])
    r = await requester.post(f"/shortages/{shortage.id}/match")
    assert (r.status_code, r.json()["code"]) == (409, "conflict")
    assert r.json()["details"] == {"open_source_request_ids": [str(request_b.id)]}


# --- the S09 hook ---------------------------------------------------------------------------


async def test_on_sources_ready_runs_once_every_planned_source_holds(
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
    now: datetime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []

    async def hook(session: AsyncSession, shortage: Shortage, run: MatchRun) -> None:
        calls.append((shortage.id, run.run_no))

    monkeypatch.setattr(service, "on_sources_ready", hook)
    ska = products["SURG-KIT-A"]
    p = await add_org(session, "Hospital P", OrgType.HOSPITAL, 12.95, 77.60)
    q = await add_org(session, "Hospital Q", OrgType.HOSPITAL, 12.96, 77.60)
    await add_batch(session, p, ska, now, on_hand=500, expiry_days=180)
    await add_batch(session, q, ska, now, on_hand=350, expiry_days=180)
    authorize(session, ska, p, q)
    split = await create_shortage(session, world.users["a.REQUESTER"], ska, now)
    by_org = {sr.source_org_id: sr for sr in await requests_of(session, split)}
    p_user = await add_user(session, p, "STORE_MANAGER", "sm@p.test")
    q_user = await add_user(session, q, "STORE_MANAGER", "sm@q.test")
    await service.accept(session, p_user, by_org[p.id].id, None, now=now)
    assert calls == []
    await service.accept(session, q_user, by_org[q.id].id, None, now=now)
    assert calls == [(split.id, 1)]


# --- listing -------------------------------------------------------------------------------------


async def test_list_incoming_and_outgoing(
    client_for: ClientFor,
    world: World,
    shortage: Shortage,
    request_b: SourceRequest,
    manager_b: httpx.AsyncClient,
) -> None:
    await manager_b.post(f"/source-requests/{request_b.id}/accept")
    incoming = (await manager_b.get("/source-requests?direction=incoming")).json()
    (item,) = incoming["items"]
    assert (item["id"], item["requester_org_name"], item["source_org_name"]) == (
        str(request_b.id),
        "Hospital A",
        "Hospital B",
    )
    assert (item["held_qty"], len(item["holds"])) == (850, 1)
    assert (await manager_b.get("/source-requests?direction=outgoing")).json()["items"] == []

    requester = await client_for(world.users["a.REQUESTER"])
    (out,) = (await requester.get("/source-requests?direction=outgoing")).json()["items"]
    assert (out["id"], out["held_qty"], out["holds"]) == (str(request_b.id), 850, None)
    assert out["product_id"] == str(shortage.product_id)
    assert (await requester.get("/source-requests?direction=incoming")).json()["items"] == []
    filtered = await requester.get(
        f"/source-requests?direction=outgoing&status=REQUESTED&shortage_id={shortage.id}"
    )
    assert filtered.json()["items"] == []

    # Another org sees neither side.
    supplier = await client_for(world.users["s.SUPPLIER_DESK"])
    for direction in ("incoming", "outgoing"):
        r = await supplier.get(f"/source-requests?direction={direction}")
        assert (r.status_code, r.json()["items"]) == (200, [])
    assert (await supplier.get("/source-requests")).status_code == 422
