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

    rows = [r for r in await audit_of(session, request_b.id) if r.action.endswith("changed")]
    by_org = {r.org_id: r for r in rows}
    assert len(rows) == 2 and set(by_org) == {b.id, world.hospital_a.id}
    for row in rows:
        assert (row.before, row.after["status"], row.reason, row.reason_source) == (  # type: ignore[index]
            {"status": "REQUESTED"},
            "TENTATIVE_HOLD",
            "OK",
            "USER",
        )
    # B's user acted, so B's org has the row with B's user (business-rules.md §10); A's trail
    # mirrors it without the user's id.
    assert by_org[b.id].actor_id == world.users["b.STORE_MANAGER"].id
    mirrored = by_org[world.hospital_a.id]
    assert mirrored.actor_id is None and "responded_by" not in (mirrored.after or {})
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
    # Expired, not declined: B is not excluded (§7 step 6) and is asked again.
    assert (run.triggered_by, run.excluded_org_ids) == ("EXPIRY", [])
    assert [(r.source_org_id, r.status) for r in await requests_of(session, shortage)] == [
        (world.hospital_b.id, "EXPIRED"),
        (world.hospital_b.id, "REQUESTED"),
    ]


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
    declined = [
        r
        for r in await audit_of(session, request_b.id)
        if r.after and r.after.get("status") == "DECLINED"
    ]
    for row in declined:
        assert (row.before, row.reason, row.reason_source) == (
            {"status": "REQUESTED"},
            "No reason was entered.",
            "SYSTEM",
        )
    # Step 8: B's decline is in A's own trail (no B user id), and in B's with B's user.
    assert {(r.org_id, r.actor_id) for r in declined} == {
        (world.hospital_a.id, None),
        (world.hospital_b.id, world.users["b.STORE_MANAGER"].id),
    }
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
    # S09: a BUY plan gets its recommendation at once (business-rules.md §7 step 4).
    assert shortage.status == "AWAITING_DECISION"
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
    assert (row.after, row.reason, row.reason_source, row.actor_id, row.org_id) == (
        {"status": "SUPERSEDED"},
        "Found stock",
        "USER",
        world.users["a.REQUESTER"].id,
        world.hospital_a.id,
    )
    # The hold release sits in B's org as a SYSTEM change with the factual cause.
    for h in await holds_of(session, request_b):
        released = (await audit_of(session, h.id))[-1]
        assert (
            released.after,
            released.actor_id,
            released.org_id,
            released.reason,
            released.reason_source,
        ) == (
            {"status": "RELEASED"},
            None,
            world.hospital_b.id,
            "The requester cancelled the shortage.",
            "SYSTEM",
        )


async def test_a_cancel_puts_none_of_the_requesters_id_or_text_in_the_source_org(
    session: AsyncSession,
    world: World,
    client_for: ClientFor,
    shortage: Shortage,
    request_b: SourceRequest,
    now: datetime,
) -> None:
    await service.accept(session, world.users["b.STORE_MANAGER"], request_b.id, "Yes", now=now)
    requester = await client_for(world.users["a.REQUESTER"])
    typed = "Cancelled by A: ward 7 found 900 kits in the back store"
    r = await requester.post(f"/shortages/{shortage.id}/cancel", json={"reason": typed})
    assert r.status_code == 200, r.text

    b_rows = list(
        await session.scalars(select(AuditLog).where(AuditLog.org_id == world.hospital_b.id))
    )
    assert b_rows, "B's accept and hold rows are expected in B's org"
    a_id = str(world.users["a.REQUESTER"].id)
    for row in b_rows:
        text = " ".join(str(x) for x in (row.actor_id, row.reason, row.before, row.after))
        assert a_id not in text and typed not in text and "ward 7" not in text, row.action
    # B's approver reads its own trail over the API: the same, plus the release cause.
    b_audit = (await (await client_for(world.users["b.APPROVER"])).get("/audit")).json()
    assert a_id not in str(b_audit) and "ward 7" not in str(b_audit)
    assert "The requester cancelled the shortage." in {x["reason"] for x in b_audit["items"]}


async def test_the_source_org_audit_shows_its_own_accept(
    client_for: ClientFor, world: World, request_b: SourceRequest, manager_b: httpx.AsyncClient
) -> None:
    r = await manager_b.post(f"/source-requests/{request_b.id}/accept", json={"reason": "Spare"})
    assert r.status_code == 200, r.text
    b_audit = await client_for(world.users["b.APPROVER"])
    items = (await b_audit.get("/audit?entity=source_request")).json()["items"]
    assert [
        (x["entity_id"], x["after"]["status"], x["actor_id"], x["reason"], x["reason_source"])
        for x in items
    ] == [
        (
            str(request_b.id),
            "TENTATIVE_HOLD",
            str(world.users["b.STORE_MANAGER"].id),
            "Spare",
            "USER",
        )
    ]
    # Hospital A's trail mirrors B's answer without B's user id or responded_by.
    a_audit = await client_for(world.users["a.APPROVER"])
    a_items = (await a_audit.get(f"/audit?entity=source_request&entity_id={request_b.id}")).json()
    answer = [x for x in a_items["items"] if x["action"] == "source_request.status_changed"]
    assert [
        (x["after"]["status"], x["actor_id"], x["reason"], x["reason_source"]) for x in answer
    ] == [("TENTATIVE_HOLD", None, "Spare", "USER")]
    assert "responded_by" not in answer[0]["after"]
    assert str(world.users["b.STORE_MANAGER"].id) not in str(a_items)


async def test_the_source_org_audit_shows_its_own_decline(
    client_for: ClientFor, world: World, request_b: SourceRequest, manager_b: httpx.AsyncClient
) -> None:
    r = await manager_b.post(
        f"/source-requests/{request_b.id}/decline", json={"reason": "Needed here"}
    )
    assert r.status_code == 200, r.text
    b_audit = await client_for(world.users["b.APPROVER"])
    items = (await b_audit.get("/audit?entity=source_request")).json()["items"]
    assert [
        (x["entity_id"], x["after"]["status"], x["actor_id"], x["reason"], x["org_id"])
        for x in items
    ] == [
        (
            str(request_b.id),
            "DECLINED",
            str(world.users["b.STORE_MANAGER"].id),
            "Needed here",
            str(world.hospital_b.id),
        )
    ]


async def test_responded_by_is_shown_to_the_source_org_only(
    client_for: ClientFor, world: World, request_b: SourceRequest, manager_b: httpx.AsyncClient
) -> None:
    accepted = await manager_b.post(f"/source-requests/{request_b.id}/accept")
    b_id = str(world.users["b.STORE_MANAGER"].id)
    assert accepted.json()["responded_by"] == b_id
    (incoming,) = (await manager_b.get("/source-requests?direction=incoming")).json()["items"]
    assert incoming["responded_by"] == b_id
    requester = await client_for(world.users["a.REQUESTER"])
    (outgoing,) = (await requester.get("/source-requests?direction=outgoing")).json()["items"]
    assert (outgoing["responded_by"], outgoing["status"]) == (None, "TENTATIVE_HOLD")
    assert outgoing["responded_at"] is not None
    assert b_id not in str(outgoing)


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


# --- released holds re-run waiting shortages (§5) -----------------------------------------------


async def test_released_holds_rerun_a_shortage_waiting_on_no_eligible_source(
    session: AsyncSession, now: datetime
) -> None:
    product = Product(
        code="REL-1",
        name="Release test kit",
        category="Test",
        unit="each",
        default_min_shelf_life_days=30,
    )
    session.add(product)
    source = await add_org(session, "Source S", OrgType.HOSPITAL, 12.93, 77.62)
    source_user = await add_user(session, source, "STORE_MANAGER", "sm@s.test")
    first = await add_org(session, "Requester One", OrgType.HOSPITAL, 12.97, 77.59)
    second = await add_org(session, "Requester Two", OrgType.HOSPITAL, 12.98, 77.60)
    first_user = await add_user(session, first, "REQUESTER", "req@one.test")
    second_user = await add_user(session, second, "REQUESTER", "req@two.test")
    await add_batch(session, source, product, now, on_hand=1000, expiry_days=180)
    authorize(session, product, source)

    held = await create_shortage(
        session, first_user, product, now, qty_required=850, qty_local_usable=0
    )
    (sr,) = await requests_of(session, held)
    await service.accept(session, source_user, sr.id, None, now=now)
    waiting = await create_shortage(
        session, second_user, product, now, qty_required=850, qty_local_usable=0
    )
    assert (await runs_of(session, waiting))[-1].planned_resolution is None  # 150 left
    assert await shortages.rematch_after_releases(session) == 0  # nothing released yet

    await shortages.cancel_shortage(session, first_user, held.id, None)  # releases 850
    assert await shortages.rematch_after_releases(session) == 1

    runs = await runs_of(session, waiting)
    assert [r.triggered_by for r in runs] == ["CREATE", "STOCK_CHANGE"]
    plan = runs[-1].planned_resolution
    assert plan is not None and plan["type"] == "TRANSFER"
    assert [line["source_org_id"] for line in plan["lines"]] == [str(source.id)]
    row = (await audit_of(session, runs[-1].id))[-1]
    assert (row.reason, row.reason_source) == (shortages.HOLDS_RELEASED, "SYSTEM")
    # The new run is newer than the release, so the same release never re-runs it again.
    assert await shortages.rematch_after_releases(session) == 0
