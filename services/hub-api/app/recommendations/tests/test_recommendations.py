"""Recommendations: creation (TRANSFER, TRANSFER_SPLIT, BUY), the explanation, hidden hospital
costs, approve / reject / escalate over the API (success, 403 from another org or without the
capability, 409 on the state machine), and expiry by the timer, a hold or a cancel."""

import json
import re
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import Product
from app.conftest import ClientFor, World
from app.domain.events import EventType
from app.domain.recommendation import about_hours, rupees
from app.notifications.models import Notification
from app.purchase_orders.models import PurchaseOrder
from app.recommendations import service
from app.recommendations.models import Recommendation
from app.recommendations.tests.conftest import (
    add_hospitals,
    answer_b,
    audit_of,
    open_rec,
    outbox,
    recs_of,
    runs_of,
)
from app.shipments.models import Shipment
from app.shortages.models import Candidate, Priority, Shortage
from app.source_requests import service as sr_service
from app.source_requests.models import Hold, SourceRequest
from app.source_requests.tests.conftest import (
    Orgs,
    add_user,
    create_shortage,
    requests_of,
)

pytestmark = pytest.mark.anyio


async def holds_of(session: AsyncSession, sr: SourceRequest) -> list[Hold]:
    stmt = select(Hold).where(Hold.source_request_id == sr.id)
    return list(await session.scalars(stmt.order_by(Hold.id)))


async def candidates_of(session: AsyncSession, rec: Recommendation) -> dict[str, Candidate]:
    stmt = select(Candidate).where(Candidate.match_run_id == rec.match_run_id)
    return {str(c.id): c for c in await session.scalars(stmt)}


@pytest.fixture
async def transfer(
    session: AsyncSession, shortage: Shortage, manager_b: httpx.AsyncClient
) -> Recommendation:
    """B accepts, so the TRANSFER from B is recommended."""
    await answer_b(session, manager_b, shortage, "accept")
    return await open_rec(session, shortage)


@pytest.fixture
async def buy(
    session: AsyncSession, shortage: Shortage, manager_b: httpx.AsyncClient
) -> Recommendation:
    """Scenario 1 step 3: B declines, so the re-run plans BUY."""
    await answer_b(session, manager_b, shortage, "decline")
    return await open_rec(session, shortage)


# --- creation --------------------------------------------------------------------------------


async def test_scenario_1_steps_4_and_5_buy_from_y_then_approve(
    session: AsyncSession,
    world: World,
    s1: Orgs,
    shortage: Shortage,
    buy: Recommendation,
    approver: httpx.AsyncClient,
) -> None:
    """After B declines: BUY from Supplier Y (earliest ETA, CRITICAL) with Supplier X as the
    alternative; approving sends the order and says so in §13's words."""
    y, x = s1["Supplier Y"], s1["Supplier X"]
    assert shortage.status == "AWAITING_DECISION"
    r = await approver.get(f"/recommendations/{buy.id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["type"], body["status"]) == ("BUY", "PENDING")
    assert [(x_["source_org_name"], x_["qty"]) for x_ in body["lines"]] == [("Supplier Y", 850)]
    assert [a["source_org_id"] for a in body["alternatives"]] == [str(x.id)]
    assert body["lines"][0]["unit_price_paise"] == 2800
    assert body["total_landed_cost_paise"] == body["lines"][0]["landed_cost_paise"]
    assert (
        "Not asked again for this shortage: Hospital B (declined the request)."
        in (body["explanation"])
    )
    (ready,) = await outbox(session, EventType.RECOMMENDATION_READY)
    assert ready["org_ids"] == [str(world.hospital_a.id)]
    assert ready["data"] == {
        "recommendation_id": str(buy.id),
        "shortage_id": str(shortage.id),
        "type": "BUY",
    }

    r = await approver.post(f"/recommendations/{buy.id}/approve", json={})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["message"] == "The order has gone to the supplier."
    assert out["shipment_ids"] == []
    assert out["recommendation"]["status"] == "APPROVED"
    assert out["recommendation"]["reason"] is None
    assert out["recommendation"]["reason_source"] == "SYSTEM"
    po = await session.get_one(PurchaseOrder, out["purchase_order_id"])
    assert (po.supplier_org_id, po.qty, po.unit_price_paise, po.status) == (y.id, 850, 2800, "SENT")
    await session.refresh(shortage)
    assert shortage.status == "IN_FULFILLMENT"
    (created,) = await outbox(session, EventType.PURCHASE_ORDER_CREATED)
    assert sorted(created["org_ids"]) == sorted([str(world.hospital_a.id), str(y.id)])
    assert created["data"] == {"purchase_order_id": str(po.id), "from": None, "to": "SENT"}

    rows = await audit_of(session, buy.id)
    assert [(r_.action, r_.after["status"]) for r_ in rows] == [  # type: ignore[index]
        ("recommendation.created", "PENDING"),
        ("recommendation.status_changed", "APPROVED"),
    ]
    approved = rows[-1]
    assert (approved.actor_id, approved.reason, approved.reason_source) == (
        world.users["a.APPROVER"].id,
        "No reason was entered.",
        "SYSTEM",
    )
    (po_row,) = await audit_of(session, po.id)
    assert (po_row.action, po_row.org_id) == ("purchase_order.created", world.hospital_a.id)


async def test_a_transfer_is_recommended_once_the_source_holds(
    session: AsyncSession, world: World, shortage: Shortage, transfer: Recommendation
) -> None:
    (sr,) = await requests_of(session, shortage)
    assert (transfer.type, transfer.status, shortage.status) == (
        "TRANSFER",
        "PENDING",
        "AWAITING_DECISION",
    )
    (line,) = transfer.lines
    assert (line["source_org_id"], line["qty"], line["source_request_id"]) == (
        str(world.hospital_b.id),
        850,
        str(sr.id),
    )
    assert line["shelf_life_days"] in (179, 180)  # +180 days, delivered within the day
    assert transfer.expires_at - transfer.created_at < timedelta(minutes=31)  # CRITICAL: 30 min
    (row, *_) = await audit_of(session, transfer.id)
    assert (row.actor_id, row.reason_source, row.org_id) == (None, "SYSTEM", world.hospital_a.id)
    assert row.reason == "Every source planned by match run 1 holds stock."


async def test_a_routine_recommendation_lasts_24_hours(
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
    s1: Orgs,
    now: datetime,
) -> None:
    buy = await create_shortage(
        session, world.users["a.REQUESTER"], products["SURG-KIT-A"], now,
        priority=Priority.ROUTINE, qty_required=2150,
    )  # fmt: skip
    rec = await open_rec(session, buy)
    assert rec.type == "BUY"
    assert rec.expires_at == now + timedelta(hours=24)


async def test_every_number_in_the_explanation_is_stored_candidate_data(
    session: AsyncSession, shortage: Shortage, transfer: Recommendation
) -> None:
    candidates = await candidates_of(session, transfer)
    (line,) = transfer.lines
    (alt,) = transfer.alternatives
    b, y = candidates[line["candidate_id"]], candidates[alt["candidate_id"]]
    # The stored lines are the candidates' figures.
    assert (line["eta_hours"], alt["eta_hours"]) == (b.eta_hours, y.eta_hours)
    assert alt["landed_cost_paise"] == y.landed_cost_paise
    text = transfer.explanation
    # Gate reasons are quoted verbatim from the rejected candidates...
    rejected = [c for c in candidates.values() if not c.eligible]
    assert len(rejected) == 3
    used = {line["candidate_id"], alt["candidate_id"]}
    other_eligible = [k for k, c in candidates.items() if c.eligible and k not in used]
    for c in rejected:
        for gate in c.gate_results:
            if not gate["passed"]:
                assert gate["reason"] in text
                text = text.replace(gate["reason"], "")
    # ...and every other number is a stored line value or the shortage's own figures.
    found = re.findall(r"₹[\d,]+\.\d{2}|\d[\d,]*", text)
    assert sorted(found) == sorted(
        [
            f"{line['qty']:,}",
            str(line["shelf_life_days"]),
            str(about_hours(line["eta_hours"])),
            str(len(other_eligible)),  # Supplier X: eligible, ranked lower
            str(len(rejected)),
            f"{alt['qty']:,}",
            rupees(alt["landed_cost_paise"]),
            str(about_hours(alt["eta_hours"])),
        ]
    )
    assert shortage.shortfall == line["qty"]


async def test_every_number_in_a_buy_explanation_is_stored_candidate_data(
    session: AsyncSession, shortage: Shortage, buy: Recommendation
) -> None:
    candidates = await candidates_of(session, buy)
    (line,) = buy.lines
    (alt,) = buy.alternatives
    assert line["landed_cost_paise"] == candidates[line["candidate_id"]].landed_cost_paise
    assert alt["landed_cost_paise"] == candidates[alt["candidate_id"]].landed_cost_paise
    text = buy.explanation
    for c in candidates.values():
        for gate in c.gate_results:
            if not gate["passed"]:
                text = text.replace(gate["reason"], "")
    found = re.findall(r"₹[\d,]+\.\d{2}|\d[\d,]*", text)
    assert sorted(found) == sorted(
        [
            "2",  # "alone or with up to 2 others" (MAX_SPLIT_SOURCES - 1)
            f"{shortage.shortfall:,}",
            f"{line['qty']:,}",
            rupees(line["landed_cost_paise"]),
            str(about_hours(line["eta_hours"])),
            "3",  # C, D and E were not eligible
            f"{alt['qty']:,}",
            rupees(alt["landed_cost_paise"]),
            str(about_hours(alt["eta_hours"])),
        ]
    )


async def test_a_hospital_cost_never_reaches_the_requester(
    session: AsyncSession, transfer: Recommendation, approver: httpx.AsyncClient
) -> None:
    """Rule 6: no figure that would reveal Hospital B's unit cost, in the response, the
    explanation, the audit trail or the events."""
    (line,) = transfer.lines
    stored = line["landed_cost_paise"]
    assert stored and stored > 0  # kept for ranking, never shown
    body = (await approver.get(f"/recommendations/{transfer.id}")).json()
    (shown,) = body["lines"]
    assert (shown["landed_cost_paise"], shown["unit_price_paise"]) == (None, None)
    assert body["total_landed_cost_paise"] is None
    assert body["alternatives"][0]["landed_cost_paise"] is not None  # a supplier's is shown
    assert rupees(stored) not in body["explanation"]
    trail = json.dumps([[r.before, r.after] for r in await audit_of(session, transfer.id)])
    assert "landed_cost" not in trail and str(stored) not in trail
    events = json.dumps(await outbox(session, EventType.RECOMMENDATION_READY))
    assert str(stored) not in events


@pytest.mark.parametrize("stock", [[500, 350], [300, 300, 250]])
async def test_approving_a_split_creates_one_shipment_per_source(
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
    now: datetime,
    client_for: ClientFor,
    approver: httpx.AsyncClient,
    stock: list[int],
) -> None:
    ska = products["SURG-KIT-A"]
    sources = await add_hospitals(session, ska, now, stock)
    split = await create_shortage(session, world.users["a.REQUESTER"], ska, now)
    for sr in await requests_of(session, split):
        org = next(o for o in sources.values() if o.id == sr.source_org_id)
        manager = await add_user(session, org, "STORE_MANAGER", f"m@{org.name[-2:]}.test")
        r = await (await client_for(manager)).post(f"/source-requests/{sr.id}/accept", json={})
        assert r.status_code == 200, r.text
    rec = await open_rec(session, split)
    assert (rec.type, len(rec.lines)) == ("TRANSFER_SPLIT", len(stock))
    assert "split across" in rec.explanation

    r = await approver.post(f"/recommendations/{rec.id}/approve", json={"reason": "Go"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["message"] == "Stock is now held at each source."
    assert out["purchase_order_id"] is None
    assert len(out["shipment_ids"]) == len(stock)
    shipments = list(
        await session.scalars(select(Shipment).where(Shipment.shortage_id == split.id))
    )
    assert sorted(s.qty for s in shipments) == sorted(stock)
    assert {s.from_org_id for s in shipments} == {o.id for o in sources.values()}
    assert all(s.to_org_id == world.hospital_a.id and s.status == "CREATED" for s in shipments)
    assert out["recommendation"]["reason"] == "Go"
    assert out["recommendation"]["reason_source"] == "USER"


async def test_approving_a_transfer_firms_holds_confirms_and_ships(
    session: AsyncSession,
    world: World,
    shortage: Shortage,
    transfer: Recommendation,
    approver: httpx.AsyncClient,
) -> None:
    r = await approver.post(f"/recommendations/{transfer.id}/approve", json={})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["message"] == "Stock is now held at the source."
    (sr,) = await requests_of(session, shortage)
    assert sr.status == "CONFIRMED"
    assert {h.status for h in await holds_of(session, sr)} == {"FIRM"}
    # Each firmed hold is recorded in the source org as SYSTEM, without the approver.
    for h in await holds_of(session, sr):
        firmed = (await audit_of(session, h.id))[-1]
        assert (firmed.after, firmed.actor_id, firmed.org_id, firmed.reason_source) == (
            {"status": "FIRM"},
            None,
            sr.source_org_id,
            "SYSTEM",
        )
        assert firmed.reason == "The requester approved the transfer."
    (shipment_id,) = out["shipment_ids"]
    shipment = await session.get_one(Shipment, shipment_id)
    assert (shipment.source_request_id, shipment.from_org_id, shipment.to_org_id) == (
        sr.id,
        world.hospital_b.id,
        world.hospital_a.id,
    )
    assert (shipment.qty, shipment.status, shipment.requires_cold_chain) == (850, "CREATED", False)
    await session.refresh(shortage)
    assert shortage.status == "IN_FULFILLMENT"
    (event,) = await outbox(session, EventType.SHIPMENT_CREATED)
    assert sorted(event["org_ids"]) == sorted([str(world.hospital_a.id), str(world.hospital_b.id)])
    (created,) = await audit_of(session, shipment.id)
    assert created.action == "shipment.created"
    statuses = [e["data"]["to"] for e in await outbox(session, EventType.SHORTAGE_STATUS_CHANGED)]
    assert statuses[-2:] == ["AWAITING_DECISION", "IN_FULFILLMENT"]


# --- 403 and 409 ----------------------------------------------------------------------------


async def test_only_the_requesting_orgs_approvers_decide(
    session: AsyncSession, world: World, transfer: Recommendation, client_for: ClientFor
) -> None:
    other = await client_for(world.users["b.APPROVER"])
    requester = await client_for(world.users["a.REQUESTER"])
    for action in ("approve", "reject", "escalate"):
        r = await other.post(f"/recommendations/{transfer.id}/{action}", json={})
        assert (r.status_code, r.json()["code"]) == (403, "forbidden")
        r = await requester.post(f"/recommendations/{transfer.id}/{action}", json={})
        assert (r.status_code, r.json()["code"]) == (403, "forbidden")
    assert (await other.get(f"/recommendations/{transfer.id}")).status_code == 403
    assert (await requester.get(f"/recommendations/{transfer.id}")).status_code == 200
    await session.refresh(transfer)
    assert transfer.status == "PENDING"


async def test_an_unknown_recommendation_is_404(approver: httpx.AsyncClient) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    assert (await approver.get(f"/recommendations/{missing}")).status_code == 404
    assert (await approver.post(f"/recommendations/{missing}/approve")).status_code == 404


@pytest.mark.parametrize("first", ["approve", "reject"])
@pytest.mark.parametrize("then", ["approve", "reject", "escalate"])
async def test_a_decided_recommendation_is_409(
    transfer: Recommendation, approver: httpx.AsyncClient, first: str, then: str
) -> None:
    assert (await approver.post(f"/recommendations/{transfer.id}/{first}")).status_code == 200
    r = await approver.post(f"/recommendations/{transfer.id}/{then}", json={})
    assert (r.status_code, r.json()["code"]) == (409, "invalid_transition")


async def test_approving_an_expired_recommendation_is_409_and_rematches(
    session: AsyncSession,
    shortage: Shortage,
    transfer: Recommendation,
    approver: httpx.AsyncClient,
    now: datetime,
) -> None:
    await session.execute(
        update(Recommendation)
        .where(Recommendation.id == transfer.id)
        .values(expires_at=now - timedelta(seconds=1))
    )
    r = await approver.post(f"/recommendations/{transfer.id}/approve", json={})
    assert (r.status_code, r.json()["code"]) == (409, "invalid_transition")
    await session.refresh(transfer)
    assert (transfer.status, transfer.reason, transfer.reason_source) == (
        "EXPIRED",
        "Recommendation validity passed.",
        "SYSTEM",
    )
    first, *later = await requests_of(session, shortage)
    assert first.status == "EXPIRED"
    assert {h.status for h in await holds_of(session, first)} == {"RELEASED"}
    run = (await runs_of(session, shortage))[-1]
    assert (run.run_no, run.triggered_by) == (2, "RECOMMENDATION_EXPIRED")
    assert [sr.status for sr in later] == ["REQUESTED"]  # B is asked again (§7 step 6)
    await session.refresh(shortage)
    assert shortage.status == "MATCHING"
    assert not await session.scalar(select(Shipment.id).where(Shipment.shortage_id == shortage.id))


async def test_approving_after_a_hold_lapsed_is_409_and_expires_the_recommendation(
    session: AsyncSession,
    shortage: Shortage,
    transfer: Recommendation,
    approver: httpx.AsyncClient,
    now: datetime,
) -> None:
    (sr,) = await requests_of(session, shortage)
    await session.execute(
        update(Hold).where(Hold.source_request_id == sr.id).values(expires_at=now)
    )
    r = await approver.post(f"/recommendations/{transfer.id}/approve", json={})
    assert (r.status_code, r.json()["code"]) == (409, "invalid_transition")
    await session.refresh(transfer)
    await session.refresh(sr)
    assert (transfer.status, transfer.reason) == ("EXPIRED", "Hold deadline passed.")
    assert sr.status == "EXPIRED"
    assert (await runs_of(session, shortage))[-1].triggered_by == "EXPIRY"


# --- reject and escalate --------------------------------------------------------------------


async def test_reject_releases_holds_and_rematches(
    session: AsyncSession,
    world: World,
    shortage: Shortage,
    transfer: Recommendation,
    approver: httpx.AsyncClient,
) -> None:
    r = await approver.post(f"/recommendations/{transfer.id}/reject", json={"reason": "Too far"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["status"], body["reason"], body["reason_source"]) == (
        "REJECTED",
        "Too far",
        "USER",
    )
    assert body["decided_by"] == str(world.users["a.APPROVER"].id)
    first, *_ = await requests_of(session, shortage)
    assert first.status == "EXPIRED"
    assert {h.status for h in await holds_of(session, first)} == {"RELEASED"}
    (*_, ended) = await audit_of(session, first.id)
    assert (ended.actor_id, ended.reason, ended.reason_source) == (
        None,
        "The recommendation was rejected.",
        "SYSTEM",
    )
    run = (await runs_of(session, shortage))[-1]
    # §7 step 6: the rejected plan's source stays out of this shortage's later runs.
    assert (run.run_no, run.triggered_by, run.excluded_org_ids) == (
        2,
        "MANUAL",
        [world.hospital_b.id],
    )
    (*_, rejected) = await audit_of(session, transfer.id)
    assert (rejected.action, rejected.reason, rejected.reason_source) == (
        "recommendation.status_changed",
        "Too far",
        "USER",
    )
    changed = await outbox(session, EventType.RECOMMENDATION_STATUS_CHANGED)
    assert [e["data"]["to"] for e in changed] == ["REJECTED"]


async def test_rejecting_a_buy_makes_a_fresh_recommendation(
    session: AsyncSession, shortage: Shortage, buy: Recommendation, approver: httpx.AsyncClient
) -> None:
    assert (await approver.post(f"/recommendations/{buy.id}/reject")).status_code == 200
    old, new = await recs_of(session, shortage)
    assert (old.id, old.status, new.status, new.type) == (buy.id, "REJECTED", "PENDING", "BUY")
    await session.refresh(shortage)
    assert shortage.status == "AWAITING_DECISION"


async def test_rejecting_excludes_the_plans_sources_from_later_runs(
    session: AsyncSession,
    world: World,
    s1: Orgs,
    shortage: Shortage,
    transfer: Recommendation,
    approver: httpx.AsyncClient,
    client_for: ClientFor,
) -> None:
    """§7 step 6: a rejected TRANSFER's hospital, then a rejected BUY's supplier, are left
    out of this shortage's later runs, as a decline is."""
    b, y, x = s1["Hospital B"], s1["Supplier Y"], s1["Supplier X"]
    other = await client_for(world.users["b.APPROVER"])
    assert (await other.post(f"/recommendations/{transfer.id}/reject")).status_code == 403
    assert (await approver.post(f"/recommendations/{transfer.id}/reject")).status_code == 200
    again = await approver.post(f"/recommendations/{transfer.id}/reject")
    assert (again.status_code, again.json()["code"]) == (409, "invalid_transition")

    second = await open_rec(session, shortage)
    run = (await runs_of(session, shortage))[-1]
    assert run.excluded_org_ids == [b.id]
    candidates = (await candidates_of(session, second)).values()
    assert b.id not in {c.source_org_id for c in candidates}
    assert (second.type, second.lines[0]["source_org_id"]) == ("BUY", str(y.id))
    assert "Hospital B (was in a recommendation the requester rejected)" in second.explanation

    assert (await approver.post(f"/recommendations/{second.id}/reject")).status_code == 200
    third = await open_rec(session, shortage)
    run = (await runs_of(session, shortage))[-1]
    assert sorted(run.excluded_org_ids, key=str) == sorted([b.id, y.id], key=str)
    assert (third.type, third.lines[0]["source_org_id"]) == ("BUY", str(x.id))


async def test_escalate_notifies_every_approver_and_can_still_be_approved(
    session: AsyncSession,
    world: World,
    buy: Recommendation,
    approver: httpx.AsyncClient,
    client_for: ClientFor,
) -> None:
    second = await add_user(session, world.hospital_a, "APPROVER", "approver2@a.test")
    other_org = world.users["b.APPROVER"]
    r = await approver.post(f"/recommendations/{buy.id}/escalate", json={"reason": "Over budget"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ESCALATED"
    notes = list(await session.scalars(select(Notification)))
    assert sorted(n.user_id for n in notes) == sorted([world.users["a.APPROVER"].id, second.id])
    assert other_org.id not in {n.user_id for n in notes}
    assert all(n.type == "recommendation.escalated" for n in notes)
    assert notes[0].payload["recommendation_id"] == str(buy.id)
    assert notes[0].payload["reason"] == "Over budget"

    again = await approver.post(f"/recommendations/{buy.id}/escalate", json={})
    assert (again.status_code, again.json()["code"]) == (409, "invalid_transition")
    r = await (await client_for(second)).post(f"/recommendations/{buy.id}/approve", json={})
    assert r.status_code == 200, r.text
    assert r.json()["recommendation"]["decided_by"] == str(second.id)
    rows = await audit_of(session, buy.id)
    assert [r_.after["status"] for r_ in rows] == ["PENDING", "ESCALATED", "APPROVED"]  # type: ignore[index]


# --- expiry ----------------------------------------------------------------------------------


async def test_the_timer_expires_a_recommendation_once(
    session: AsyncSession, shortage: Shortage, transfer: Recommendation
) -> None:
    before = transfer.expires_at - timedelta(seconds=1)
    assert await service.expire_overdue(session, now=before) == 0
    assert await service.expire_overdue(session, now=transfer.expires_at) == 1
    assert await service.expire_overdue(session, now=transfer.expires_at) == 0
    await session.refresh(transfer)
    assert (transfer.status, transfer.decided_by) == ("EXPIRED", None)
    run = (await runs_of(session, shortage))[-1]
    assert run.triggered_by == "RECOMMENDATION_EXPIRED"
    (*_, row) = await audit_of(session, transfer.id)
    assert (row.actor_id, row.reason, row.reason_source) == (
        None,
        "Recommendation validity passed.",
        "SYSTEM",
    )


async def test_a_lapsed_hold_expires_the_open_recommendation(
    session: AsyncSession, shortage: Shortage, transfer: Recommendation
) -> None:
    (sr,) = await requests_of(session, shortage)
    (hold,) = await holds_of(session, sr)
    assert await sr_service.expire_overdue(session, now=hold.expires_at) == 1
    await session.refresh(transfer)
    assert (transfer.status, transfer.reason, transfer.reason_source) == (
        "EXPIRED",
        "Hold deadline passed.",
        "SYSTEM",
    )


async def test_cancelling_the_shortage_expires_the_open_recommendation(
    session: AsyncSession,
    world: World,
    shortage: Shortage,
    transfer: Recommendation,
    client_for: ClientFor,
) -> None:
    requester = await client_for(world.users["a.REQUESTER"])
    r = await requester.post(f"/shortages/{shortage.id}/cancel", json={"reason": "Found stock"})
    assert r.status_code == 200, r.text
    await session.refresh(transfer)
    assert (transfer.status, transfer.reason, transfer.reason_source) == (
        "EXPIRED",
        "The shortage was cancelled.",
        "SYSTEM",
    )
    (sr,) = await requests_of(session, shortage)
    assert sr.status == "SUPERSEDED"
