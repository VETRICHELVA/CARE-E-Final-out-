"""The outbox: events written with their state change (and never without it), the events the
S04-S06 transitions emit, the stock-change re-run (business-rules.md §5) and the worker jobs."""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.models import AuditLog
from app.catalog.models import Product
from app.conftest import ClientFor, World
from app.domain.events import EventType
from app.events import service as events
from app.events.models import EventOutbox
from app.shortages.models import MatchRun, Shortage, Trigger
from app.source_requests.tests.conftest import (
    add_batch,
    authorize,
    create_shortage,
    facility_of,
    requests_of,
    seed_scenario1,
)
from app.worker import publish_events

pytestmark = pytest.mark.anyio
Maker = async_sessionmaker[AsyncSession]


async def outbox(session: AsyncSession, *types: str) -> list[EventOutbox]:
    stmt = select(EventOutbox).order_by(EventOutbox.created_at, EventOutbox.id)
    if types:
        stmt = stmt.where(EventOutbox.event_type.in_(types))
    return list(await session.scalars(stmt))


def data(e: EventOutbox) -> dict[str, Any]:
    result: dict[str, Any] = e.payload["data"]
    return result


async def drain(pubsub: Any, quiet: float = 0.3) -> list[dict[str, Any]]:
    """Every message until none arrives for `quiet` seconds (subscribe confirmations are
    read as None, so a single None does not mean the channel is quiet)."""
    got: list[dict[str, Any]] = []
    loop = asyncio.get_running_loop()
    until = loop.time() + quiet
    while (left := until - loop.time()) > 0:
        m = await pubsub.get_message(ignore_subscribe_messages=True, timeout=left)
        if m is not None:
            got.append(json.loads(m["data"]))
            until = loop.time() + quiet
    return got


# --- the outbox and the publisher --------------------------------------------------------------


async def test_emit_writes_the_envelope_and_publish_sends_it_to_each_addressed_org(
    session: AsyncSession, redis: Redis
) -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    sub = redis.pubsub()
    await sub.subscribe(events.channel(a), events.channel(b))
    event = await events.emit(session, EventType.SHORTAGE_STATUS_CHANGED, [b, a, a], {"k": 1})
    assert event.org_ids == sorted([a, b], key=str)
    assert event.payload["id"] == str(event.id)
    assert event.payload["type"] == "shortage.status_changed"
    assert datetime.fromisoformat(event.payload["occurred_at"]).tzinfo is not None
    assert (event.published_at, event.seq) == (None, None)

    assert await events.publish_pending(session, redis) == 1
    assert event.published_at is not None and event.seq is not None
    got = await drain(sub)
    assert [(m["seq"], m["event"]["id"]) for m in got] == [(event.seq, str(event.id))] * 2
    assert await events.publish_pending(session, redis) == 0  # published once
    await sub.aclose()  # type: ignore[no-untyped-call]
    with pytest.raises(ValueError):
        await events.emit(session, EventType.SHORTAGE_STATUS_CHANGED, [], {})


async def test_publish_order_is_the_seq_order(session: AsyncSession, redis: Redis) -> None:
    org = uuid.uuid4()
    emitted = [
        await events.emit(session, EventType.INVENTORY_CHANGED, [org], {"n": n}) for n in range(5)
    ]
    await events.publish_pending(session, redis)
    seqs = [e.seq or 0 for e in emitted]
    assert seqs == sorted(seqs) and len(set(seqs)) == 5 and 0 not in seqs


async def test_an_event_in_a_rolled_back_transaction_is_never_published(
    committed: Maker, redis: Redis
) -> None:
    """Real transactions on the committed database: the rolled-back event leaves no outbox row,
    so the publisher never sees it; the committed one is published."""
    async with committed() as session:
        await events.publish_pending(session, redis)  # whatever earlier tests left behind
    org = uuid.uuid4()
    sub = redis.pubsub()
    await sub.subscribe(events.channel(org))

    async with committed() as session:
        rolled = await events.emit(session, EventType.SHORTAGE_STATUS_CHANGED, [org], {"n": 1})
        await session.rollback()
    async with committed() as session:
        kept = await events.emit(session, EventType.SHORTAGE_STATUS_CHANGED, [org], {"n": 2})
        await session.commit()
    async with committed() as session:
        assert await events.publish_pending(session, redis) >= 1
        assert await session.get(EventOutbox, rolled.id) is None
        assert (await session.get_one(EventOutbox, kept.id)).seq is not None

    assert [m["event"]["id"] for m in await drain(sub)] == [str(kept.id)]
    await sub.aclose()  # type: ignore[no-untyped-call]


async def test_an_uncommitted_event_is_not_published_until_it_commits(
    committed: Maker, redis: Redis
) -> None:
    org = uuid.uuid4()
    sub = redis.pubsub()
    await sub.subscribe(events.channel(org))
    async with committed() as writer, committed() as publisher:
        pending = await events.emit(writer, EventType.SHORTAGE_STATUS_CHANGED, [org], {})
        await events.publish_pending(publisher, redis)
        assert await drain(sub) == []  # invisible to the publisher while uncommitted
        await writer.commit()
        assert await events.publish_pending(publisher, redis) >= 1
    assert [m["event"]["id"] for m in await drain(sub)] == [str(pending.id)]
    await sub.aclose()  # type: ignore[no-untyped-call]


async def test_the_worker_job_publishes_committed_events(committed: Maker, redis: Redis) -> None:
    async with committed() as session:
        event = await events.emit(session, EventType.SHORTAGE_STATUS_CHANGED, [uuid.uuid4()], {})
        await session.commit()
    ctx = {"sessionmaker": committed, "redis": redis}
    # Two workers at once: the advisory lock lets one publish, and nothing goes out twice.
    results = await asyncio.gather(publish_events(ctx), publish_events(ctx))
    assert sum(results) >= 1
    async with committed() as session:
        assert (await session.get_one(EventOutbox, event.id)).seq is not None


# --- retrofitted events ------------------------------------------------------------------------


async def scenario1(session: AsyncSession, world: World, products: dict[str, Product]) -> Shortage:
    """Scenario 1 steps 1-2: A is 850 short (CRITICAL); the plan is a TRANSFER from B."""
    now = datetime.now(UTC)
    await seed_scenario1(session, world.hospital_a, world.hospital_b, products["SURG-KIT-A"], now)
    return await create_shortage(session, world.users["a.REQUESTER"], products["SURG-KIT-A"], now)


async def test_creating_a_shortage_emits_its_transition_and_the_request(
    session: AsyncSession, world: World, products: dict[str, Product]
) -> None:
    a, b = world.hospital_a, world.hospital_b
    shortage = await scenario1(session, world, products)
    (status,) = await outbox(session, EventType.SHORTAGE_STATUS_CHANGED)
    assert status.org_ids == [a.id]  # the shortage is A's alone
    assert data(status) == {"shortage_id": str(shortage.id), "from": "OPEN", "to": "MATCHING"}
    (created,) = await outbox(session, EventType.SOURCE_REQUEST_CREATED)
    (sr,) = await requests_of(session, shortage)
    assert set(created.org_ids) == {a.id, b.id}
    assert data(created) == {
        "source_request_id": str(sr.id),
        "shortage_id": str(shortage.id),
        "product_id": str(shortage.product_id),
        "qty": 850,
        "deadline": sr.sla_deadline.isoformat(),
    }


async def test_a_decline_emits_to_both_orgs(
    session: AsyncSession, world: World, products: dict[str, Product], client_for: ClientFor
) -> None:
    a, b = world.hospital_a, world.hospital_b
    shortage = await scenario1(session, world, products)
    (sr,) = await requests_of(session, shortage)
    manager_b = await client_for(world.users["b.STORE_MANAGER"])
    assert (await manager_b.post(f"/source-requests/{sr.id}/decline")).status_code == 200
    (changed,) = await outbox(session, EventType.SOURCE_REQUEST_STATUS_CHANGED)
    assert set(changed.org_ids) == {a.id, b.id}
    assert data(changed) == {"source_request_id": str(sr.id), "from": "REQUESTED", "to": "DECLINED"}


async def test_a_cancel_emits_the_shortage_transition_and_each_superseded_request(
    session: AsyncSession, world: World, products: dict[str, Product], client_for: ClientFor
) -> None:
    a, b = world.hospital_a, world.hospital_b
    shortage = await scenario1(session, world, products)
    (sr,) = await requests_of(session, shortage)
    requester = await client_for(world.users["a.REQUESTER"])
    assert (await requester.post(f"/shortages/{shortage.id}/cancel")).status_code == 200
    last = (await outbox(session, EventType.SHORTAGE_STATUS_CHANGED))[-1]
    assert last.org_ids == [a.id]
    assert data(last) == {"shortage_id": str(shortage.id), "from": "MATCHING", "to": "CANCELLED"}
    (superseded,) = await outbox(session, EventType.SOURCE_REQUEST_STATUS_CHANGED)
    assert set(superseded.org_ids) == {a.id, b.id}
    assert data(superseded) == {
        "source_request_id": str(sr.id),
        "from": "REQUESTED",
        "to": "SUPERSEDED",
    }


async def test_a_request_transition_writes_one_audit_row_and_one_event(
    session: AsyncSession, world: World, products: dict[str, Product], client_for: ClientFor
) -> None:
    shortage = await scenario1(session, world, products)
    (sr,) = await requests_of(session, shortage)
    manager_b = await client_for(world.users["b.STORE_MANAGER"])
    assert (await manager_b.post(f"/source-requests/{sr.id}/accept")).status_code == 200
    audited = list(
        await session.scalars(
            select(AuditLog).where(
                AuditLog.entity_id == sr.id, AuditLog.action == "source_request.status_changed"
            )
        )
    )
    events_ = await outbox(session, EventType.SOURCE_REQUEST_STATUS_CHANGED)
    assert [(r.before, r.after["status"]) for r in audited if r.after] == [
        ({"status": "REQUESTED"}, "TENTATIVE_HOLD")
    ]
    assert [(data(e)["from"], data(e)["to"]) for e in events_] == [("REQUESTED", "TENTATIVE_HOLD")]


async def test_inventory_and_offer_writes_emit_to_their_own_org_only(
    session: AsyncSession, world: World, products: dict[str, Product], client_for: ClientFor
) -> None:
    ska = products["SURG-KIT-A"]
    facility = await facility_of(session, world.hospital_b)
    manager_b = await client_for(world.users["b.STORE_MANAGER"])
    expiry = str((datetime.now(UTC) + timedelta(days=200)).date())
    r = await manager_b.post(
        "/inventory/batches",
        json={
            "facility_id": str(facility.id),
            "product_id": str(ska.id),
            "batch_no": "EV-1",
            "on_hand": 100,
            "expiry_date": expiry,
            "unit_cost_paise": 1500,
        },
    )
    assert r.status_code == 201, r.text
    batch_id = r.json()["id"]
    assert (
        await manager_b.patch(f"/inventory/batches/{batch_id}", json={"on_hand": 90})
    ).is_success
    r = await manager_b.post(
        f"/inventory/batches/{batch_id}/verify", json={"method": "MANUAL", "counted_qty": 90}
    )
    assert r.is_success
    csv = (
        f"product_code,batch_no,on_hand,expiry_date,unit_cost_paise\nSURG-KIT-A,EV-2,5,{expiry},1\n"
    )
    r = await manager_b.post(
        "/inventory/batches/import",
        params={"facility_id": str(facility.id)},
        content=csv,
        headers={"Content-Type": "text/csv"},
    )
    assert r.json()["inserted"] == 1
    # An unchanged PATCH changes nothing, so it emits nothing.
    assert (
        await manager_b.patch(f"/inventory/batches/{batch_id}", json={"on_hand": 90})
    ).is_success

    changed = await outbox(session, EventType.INVENTORY_CHANGED)
    assert len(changed) == 4
    assert {tuple(e.org_ids) for e in changed} == {(world.hospital_b.id,)}
    assert data(changed[0]) == {"batch_ids": [batch_id], "product_ids": [str(ska.id)]}

    desk = await client_for(world.users["s.SUPPLIER_DESK"])
    r = await desk.put(
        "/supplier-offers",
        json={
            "product_id": str(ska.id),
            "unit_price_paise": 1400,
            "lead_time_hours": 24,
            "available_qty": 10,
        },
    )
    assert r.status_code == 200, r.text
    (offer,) = await outbox(session, EventType.SUPPLIER_OFFER_CHANGED)
    assert offer.org_ids == [world.supplier.id]
    assert data(offer) == {"offer_id": r.json()["id"], "product_id": str(ska.id)}


# --- §5 re-run when inventory or offers change --------------------------------------------------


async def runs_of(session: AsyncSession, shortage: Shortage) -> list[MatchRun]:
    stmt = select(MatchRun).where(MatchRun.shortage_id == shortage.id)
    return list(await session.scalars(stmt.order_by(MatchRun.run_no)))


async def test_a_waiting_shortage_re_runs_when_another_org_s_stock_changes(
    session: AsyncSession, world: World, products: dict[str, Product], client_for: ClientFor
) -> None:
    now = datetime.now(UTC)
    ska = products["SURG-KIT-A"]
    authorize(session, ska, world.hospital_b, world.supplier)
    shortage = await create_shortage(
        session, world.users["a.REQUESTER"], ska, now, qty_required=100, qty_local_usable=0
    )
    (first,) = await runs_of(session, shortage)
    assert first.planned_resolution is None  # "No eligible source"
    assert shortage.status == "MATCHING"

    # A's own stock is never a candidate for A, so A's batch change does not re-run it.
    manager_a = await client_for(world.users["a.STORE_MANAGER"])
    facility_a = await facility_of(session, world.hospital_a)
    expiry = str((now + timedelta(days=200)).date())
    batch = {
        "product_id": str(ska.id),
        "on_hand": 500,
        "expiry_date": expiry,
        "unit_cost_paise": 1500,
    }
    r = await manager_a.post(
        "/inventory/batches", json={**batch, "facility_id": str(facility_a.id), "batch_no": "A-1"}
    )
    assert r.status_code == 201
    assert len(await runs_of(session, shortage)) == 1

    # B adds stock: the shortage re-runs, but B's new batch was never verified (freshness).
    manager_b = await client_for(world.users["b.STORE_MANAGER"])
    facility_b = await facility_of(session, world.hospital_b)
    r = await manager_b.post(
        "/inventory/batches", json={**batch, "facility_id": str(facility_b.id), "batch_no": "B-9"}
    )
    assert r.status_code == 201
    runs = await runs_of(session, shortage)
    assert [(x.run_no, x.triggered_by, x.planned_resolution) for x in runs[1:]] == [
        (2, Trigger.STOCK_CHANGE, None)
    ]

    # B counts it: now B is eligible, so the re-run plans a TRANSFER and asks B.
    batch_b = r.json()["id"]
    r = await manager_b.post(
        f"/inventory/batches/{batch_b}/verify", json={"method": "SCAN", "counted_qty": 500}
    )
    assert r.status_code == 200
    third = (await runs_of(session, shortage))[-1]
    assert (third.run_no, third.triggered_by) == (3, Trigger.STOCK_CHANGE)
    assert third.planned_resolution is not None and third.planned_resolution["type"] == "TRANSFER"
    (sr,) = await requests_of(session, shortage)
    assert (sr.source_org_id, sr.qty, sr.status) == (world.hospital_b.id, 100, "REQUESTED")
    created = await outbox(session, EventType.SOURCE_REQUEST_CREATED)
    assert {world.hospital_b.id, world.hospital_a.id} == set(created[-1].org_ids)

    # It has a plan and an open request now, so further changes leave it alone.
    await manager_b.patch(f"/inventory/batches/{batch_b}", json={"on_hand": 450})
    assert len(await runs_of(session, shortage)) == 3


async def test_a_supplier_offer_change_re_runs_a_waiting_shortage(
    session: AsyncSession, world: World, products: dict[str, Product], client_for: ClientFor
) -> None:
    now = datetime.now(UTC)
    ska = products["SURG-KIT-A"]
    authorize(session, ska, world.supplier)
    shortage = await create_shortage(
        session, world.users["a.REQUESTER"], ska, now, qty_required=100, qty_local_usable=0
    )
    desk = await client_for(world.users["s.SUPPLIER_DESK"])
    r = await desk.put(
        "/supplier-offers",
        json={
            "product_id": str(ska.id),
            "unit_price_paise": 1400,
            "lead_time_hours": 24,
            "available_qty": 1000,
        },
    )
    assert r.status_code == 200
    runs = await runs_of(session, shortage)
    assert [x.triggered_by for x in runs] == [Trigger.CREATE, Trigger.STOCK_CHANGE]
    assert runs[-1].planned_resolution is not None
    assert runs[-1].planned_resolution["type"] == "BUY"
    # The run is audited as a system action with its factual cause.
    audit = await session.scalar(select(AuditLog).where(AuditLog.entity_id == runs[-1].id))
    assert audit is not None
    assert (audit.actor_id, audit.reason, audit.org_id) == (
        None,
        "Inventory or supplier offers for this product changed.",
        world.hospital_a.id,
    )


async def test_only_waiting_shortages_of_the_changed_product_re_run(
    session: AsyncSession, world: World, products: dict[str, Product], client_for: ClientFor
) -> None:
    now = datetime.now(UTC)
    ska, cannula = products["SURG-KIT-A"], products["IV-CAN-20G"]
    other = await create_shortage(
        session, world.users["a.REQUESTER"], cannula, now, qty_required=10, qty_local_usable=0
    )
    cancelled = await create_shortage(
        session, world.users["a.REQUESTER"], ska, now, qty_required=10, qty_local_usable=0
    )
    requester = await client_for(world.users["a.REQUESTER"])
    assert (await requester.post(f"/shortages/{cancelled.id}/cancel")).status_code == 200
    await add_batch(session, world.hospital_b, ska, now, on_hand=10, expiry_days=200, batch_no="X")
    desk = await client_for(world.users["s.SUPPLIER_DESK"])
    offer = {
        "product_id": str(ska.id),
        "unit_price_paise": 1,
        "lead_time_hours": 1,
        "available_qty": 1,
    }
    assert (await desk.put("/supplier-offers", json=offer)).status_code == 200
    assert len(await runs_of(session, other)) == 1
    assert len(await runs_of(session, cancelled)) == 1
