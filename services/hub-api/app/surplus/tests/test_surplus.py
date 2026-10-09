"""Surplus posts (S18): create, withdraw, expire, what is offered, and matching to other
orgs' open shortages and forecast stock-outs."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.catalog.models import Product
from app.conftest import ClientFor, World
from app.events.models import EventOutbox
from app.surplus import service
from app.surplus.models import SurplusMatch, SurplusPost
from app.surplus.tests.conftest import (
    add_batch,
    add_forecast,
    add_hospital,
    add_shortage,
    today,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def iv(products: dict[str, Product]) -> Product:
    return products["IV-CAN-20G"]


async def matched_events(session: AsyncSession) -> list[EventOutbox]:
    return list(
        await session.scalars(
            select(EventOutbox).where(EventOutbox.event_type == "surplus.matched")
        )
    )


async def test_create_offers_up_to_transferable_and_is_audited(
    session: AsyncSession, world: World, iv: Product, client_for: ClientFor
) -> None:
    batch = await add_batch(session, world.hospital_b, iv, on_hand=1000, safety_stock=200,
                            expiry_days=55)  # fmt: skip
    client = await client_for(world.users["b.STORE_MANAGER"])
    r = await client.post("/surplus", json={"batch_id": str(batch.id), "qty": 300})
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["qty"], body["offered_qty"], body["status"]) == (300, 300, "OPEN")
    assert body["expiry_date"] == str(batch.expiry_date)
    assert body["matched_org_ids"] == []  # nobody needs IV Cannula 20G
    row = await session.scalar(
        select(AuditLog).where(AuditLog.entity == "surplus_post", AuditLog.action.like("%created"))
    )
    assert row is not None and (row.reason_source, row.org_id) == ("SYSTEM", world.hospital_b.id)

    # Rule 4: stock reserved later shrinks the offer to the batch's transferable now.
    batch.reserved = 650  # 1000 - 650 - 200 = 150 transferable
    await session.flush()
    r = await client.get("/surplus")
    assert [(p["qty"], p["offered_qty"]) for p in r.json()["items"]] == [(300, 150)]


async def test_create_refusals(
    session: AsyncSession, world: World, iv: Product, client_for: ClientFor
) -> None:
    batch = await add_batch(session, world.hospital_b, iv, on_hand=1000, safety_stock=200,
                            expiry_days=55)  # fmt: skip
    expired = await add_batch(session, world.hospital_b, iv, on_hand=50, expiry_days=0,
                              batch_no="IV-OLD")  # fmt: skip
    b = await client_for(world.users["b.STORE_MANAGER"])
    a = await client_for(world.users["a.STORE_MANAGER"])

    r = await a.post("/surplus", json={"batch_id": str(batch.id), "qty": 10})
    assert (r.status_code, r.json()["code"]) == (403, "forbidden")  # another org's batch
    r = await b.post("/surplus", json={"batch_id": str(batch.id), "qty": 801})
    assert (r.status_code, r.json()["details"]["transferable_qty"]) == (400, 800)
    r = await b.post("/surplus", json={"batch_id": str(expired.id), "qty": 10})
    assert (r.status_code, r.json()["details"]["reason"]) == (400, "expired")
    r = await b.post("/surplus", json={"batch_id": str(batch.id), "qty": 0})
    assert r.status_code == 422
    assert (
        await b.post("/surplus", json={"batch_id": str(batch.id), "qty": 10})
    ).status_code == 201
    r = await b.post("/surplus", json={"batch_id": str(batch.id), "qty": 10})
    assert (r.status_code, r.json()["code"]) == (409, "conflict")  # one live post per batch
    # Capability and org type: an approver has no inventory.edit; a supplier holds no batches.
    approver = await client_for(world.users["b.APPROVER"])
    assert (await approver.get("/surplus")).status_code == 403
    supplier = await client_for(world.users["s.ADMIN"])
    assert (await supplier.get("/surplus")).status_code == 403


async def test_withdraw(
    session: AsyncSession, world: World, iv: Product, client_for: ClientFor
) -> None:
    batch = await add_batch(session, world.hospital_b, iv, on_hand=500, expiry_days=55)
    b = await client_for(world.users["b.STORE_MANAGER"])
    post = (await b.post("/surplus", json={"batch_id": str(batch.id), "qty": 100})).json()
    a = await client_for(world.users["a.STORE_MANAGER"])
    r = await a.post(f"/surplus/{post['id']}/withdraw")
    assert r.status_code == 403
    r = await b.post(f"/surplus/{post['id']}/withdraw", json={"reason": "Used it ourselves."})
    assert (r.status_code, r.json()["status"]) == (200, "WITHDRAWN")
    r = await b.post(f"/surplus/{post['id']}/withdraw")
    assert (r.status_code, r.json()["code"]) == (409, "invalid_transition")
    row = await session.scalar(select(AuditLog).where(AuditLog.action == "surplus_post.withdrawn"))
    assert row is not None and (row.reason, row.reason_source) == ("Used it ourselves.", "USER")
    # The batch can be posted again once its post is withdrawn.
    r = await b.post("/surplus", json={"batch_id": str(batch.id), "qty": 100})
    assert r.status_code == 201


async def test_a_post_matches_an_open_shortage_and_the_matched_org_sees_only_rule_6_fields(
    session: AsyncSession, world: World, iv: Product, client_for: ClientFor
) -> None:
    shortage = await add_shortage(session, world.hospital_a, world.users["a.REQUESTER"], iv)
    batch = await add_batch(session, world.hospital_b, iv, on_hand=1000, safety_stock=200,
                            expiry_days=55)  # fmt: skip
    b = await client_for(world.users["b.STORE_MANAGER"])
    post = (await b.post("/surplus", json={"batch_id": str(batch.id), "qty": 300})).json()
    assert (post["status"], post["matched_org_ids"]) == ("MATCHED", [str(world.hospital_a.id)])

    match = await session.scalar(select(SurplusMatch))
    assert match is not None and (match.kind, match.shortage_id) == ("SHORTAGE", shortage.id)
    (event,) = await matched_events(session)
    assert set(event.org_ids) == {world.hospital_a.id, world.hospital_b.id}
    assert event.payload["data"] == {"surplus_id": post["id"], "org_id": str(world.hospital_a.id)}
    audit = await session.scalar(select(AuditLog).where(AuditLog.action == "surplus_post.matched"))
    assert audit is not None
    assert (audit.reason_source, audit.org_id, audit.actor_id) == (
        "SYSTEM",
        world.hospital_b.id,
        None,
    )

    a = await client_for(world.users["a.STORE_MANAGER"])
    (offer,) = (await a.get("/surplus/incoming")).json()["items"]
    assert set(offer) == {
        "id", "org_id", "org_name", "product_id", "offered_qty", "expiry_band", "location",
        "status", "match",
    }  # fmt: skip
    assert (offer["org_name"], offer["offered_qty"], offer["expiry_band"]) == (
        "Hospital B",
        300,
        "30_TO_59_DAYS",
    )
    assert offer["location"]["lat"] == world.hospital_b.lat
    assert offer["match"]["kind"] == "SHORTAGE"
    assert offer["match"]["shortage_id"] == str(shortage.id)
    # B's own list is not A's, and B sees nothing incoming.
    assert (await a.get("/surplus")).json()["items"] == []
    assert (await b.get("/surplus/incoming")).json()["items"] == []
    # Matching again adds nothing: each org is matched once.
    assert await service.match_open(session, today()) == 0
    assert len(await matched_events(session)) == 1


async def test_a_post_matches_a_forecast_stockout_within_14_days_only(
    session: AsyncSession, world: World, iv: Product, client_for: ClientFor
) -> None:
    e, _ = await add_hospital(session, "Hospital E", 13.0, 77.74)
    f, _ = await add_hospital(session, "Hospital F", 12.9, 77.70)
    await add_batch(session, e, iv, on_hand=120, expiry_days=200)  # 4 days at 30 a day
    await add_forecast(session, e, iv, 30)
    await add_batch(session, f, iv, on_hand=450, expiry_days=200)  # 15 days: too far
    await add_forecast(session, f, iv, 30)
    batch = await add_batch(session, world.hospital_b, iv, on_hand=1000, safety_stock=200,
                            expiry_days=55)  # fmt: skip
    b = await client_for(world.users["b.STORE_MANAGER"])
    post = (await b.post("/surplus", json={"batch_id": str(batch.id), "qty": 300})).json()
    assert post["matched_org_ids"] == [str(e.id)]
    match = await session.scalar(select(SurplusMatch))
    assert match is not None
    assert (match.kind, match.stockout_date) == ("FORECAST", today() + timedelta(days=4))


async def test_a_withdrawn_post_is_never_matched(
    session: AsyncSession, world: World, iv: Product, client_for: ClientFor
) -> None:
    batch = await add_batch(session, world.hospital_b, iv, on_hand=1000, expiry_days=55)
    b = await client_for(world.users["b.STORE_MANAGER"])
    post = (await b.post("/surplus", json={"batch_id": str(batch.id), "qty": 300})).json()
    assert post["status"] == "OPEN"
    await b.post(f"/surplus/{post['id']}/withdraw")
    await add_shortage(session, world.hospital_a, world.users["a.REQUESTER"], iv)
    assert await service.match_open(session, today()) == 0
    assert await service.match(session, [uuid.UUID(post["id"])], today()) == 0
    assert await session.scalar(select(SurplusMatch)) is None
    assert await matched_events(session) == []


async def test_nothing_on_offer_is_not_matched(
    session: AsyncSession, world: World, iv: Product, client_for: ClientFor
) -> None:
    batch = await add_batch(session, world.hospital_b, iv, on_hand=500, expiry_days=55)
    b = await client_for(world.users["b.STORE_MANAGER"])
    post = (await b.post("/surplus", json={"batch_id": str(batch.id), "qty": 300})).json()
    batch.reserved = 500  # nothing transferable any more
    await session.flush()
    await add_shortage(session, world.hospital_a, world.users["a.REQUESTER"], iv)
    assert await service.match_open(session, today()) == 0
    assert (await b.get("/surplus")).json()["items"][0]["offered_qty"] == 0
    assert post["status"] == "OPEN"


async def test_posts_expire_at_batch_expiry(
    session: AsyncSession, world: World, iv: Product
) -> None:
    batch = await add_batch(session, world.hospital_b, iv, on_hand=500, expiry_days=3)
    post = SurplusPost(
        org_id=world.hospital_b.id,
        batch_id=batch.id,
        product_id=iv.id,
        qty=100,
        expiry_date=batch.expiry_date,
        status="OPEN",
        created_by=world.users["b.STORE_MANAGER"].id,
    )
    session.add(post)
    await session.flush()
    assert await service.expire_due(session, today() + timedelta(days=2)) == 0
    assert await service.expire_due(session, batch.expiry_date) == 1
    assert post.status == "EXPIRED"
    row = await session.scalar(select(AuditLog).where(AuditLog.action == "surplus_post.expired"))
    assert row is not None
    assert (row.reason, row.reason_source) == ("The batch reached its expiry date.", "SYSTEM")
    await add_shortage(session, world.hospital_a, world.users["a.REQUESTER"], iv)
    assert await service.match_open(session, today()) == 0


async def test_a_corrected_batch_expiry_moves_its_live_post(
    session: AsyncSession, world: World, iv: Product, client_for: ClientFor
) -> None:
    """From the S18 review: the band other orgs see, the post's expiry day and its matches
    follow the batch's current expiry date, not the one it had when posted."""
    await add_shortage(session, world.hospital_a, world.users["a.REQUESTER"], iv)
    batch = await add_batch(session, world.hospital_b, iv, on_hand=1000, safety_stock=200,
                            expiry_days=55)  # fmt: skip
    b = await client_for(world.users["b.STORE_MANAGER"])
    post = (await b.post("/surplus", json={"batch_id": str(batch.id), "qty": 300})).json()
    corrected = today() + timedelta(days=20)
    r = await b.patch(f"/inventory/batches/{batch.id}", json={"expiry_date": str(corrected)})
    assert r.status_code == 200, r.text

    a = await client_for(world.users["a.STORE_MANAGER"])
    (offer,) = (await a.get("/surplus/incoming")).json()["items"]
    assert offer["expiry_band"] == "UNDER_30_DAYS"
    stored = await session.get_one(SurplusPost, uuid.UUID(post["id"]))
    assert stored.expiry_date == corrected
    row = await session.scalar(
        select(AuditLog).where(
            AuditLog.entity_id == stored.id, AuditLog.action == "surplus_post.updated"
        )
    )
    assert row is not None
    assert (row.reason_source, row.org_id, row.actor_id) == ("SYSTEM", world.hospital_b.id, None)
