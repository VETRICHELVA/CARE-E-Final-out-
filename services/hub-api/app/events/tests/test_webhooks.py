"""Webhook subscriptions (org admin only, own org only) and deliveries: per-org fan-out,
the HMAC signature, and the retry schedule with a frozen clock (`now` is passed in)."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.conftest import ClientFor, World
from app.domain import webhooks as rules
from app.domain.events import EventType
from app.domain.webhooks import DeliveryStatus
from app.events import service as events
from app.events import webhooks
from app.events.models import EventOutbox, WebhookDelivery, WebhookSubscription

pytestmark = pytest.mark.anyio

T0 = datetime(2026, 10, 7, 6, 0, tzinfo=UTC)
STATUS = EventType.SHORTAGE_STATUS_CHANGED
HOOK = {"url": "https://hooks.example.test/care-e", "event_types": [STATUS]}


class Receiver:
    """A fake webhook receiver: records each request and answers with `codes` in turn."""

    def __init__(self, *codes: int | None) -> None:
        self.codes = list(codes)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        code = self.codes.pop(0) if len(self.codes) > 1 else self.codes[0]
        if code is None:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(code)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


async def subscribe(
    session: AsyncSession, client_for: ClientFor, world: World, admin: str, **body: object
) -> dict[str, object]:
    r = await (await client_for(world.users[admin])).post("/webhooks", json={**HOOK, **body})
    assert r.status_code == 201, r.text
    result: dict[str, object] = r.json()
    return result


async def deliveries(session: AsyncSession, sub_id: object) -> list[WebhookDelivery]:
    stmt = select(WebhookDelivery).where(WebhookDelivery.subscription_id == uuid.UUID(str(sub_id)))
    return list(await session.scalars(stmt.order_by(WebhookDelivery.attempt)))


# --- endpoints ---------------------------------------------------------------------------------


async def test_an_org_admin_creates_lists_and_deletes_subscriptions(
    session: AsyncSession, world: World, client_for: ClientFor
) -> None:
    admin = await client_for(world.users["a.ADMIN"])
    r = await admin.post("/webhooks", json={**HOOK, "reason": "ERP integration"})
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["url"] == HOOK["url"]
    assert created["event_types"] == [STATUS]
    assert len(created["secret"]) >= 32

    r = await admin.get("/webhooks")
    assert r.status_code == 200
    (listed,) = r.json()["items"]
    assert listed["id"] == created["id"]
    assert "secret" not in listed  # shown once, at creation

    audit = await session.scalar(
        select(AuditLog).where(AuditLog.entity_id == uuid.UUID(created["id"]))
    )
    assert audit is not None
    assert (audit.action, audit.reason, audit.reason_source) == (
        "webhook_subscription.created",
        "ERP integration",
        "USER",
    )
    assert created["secret"] not in json.dumps(audit.after)

    assert (await admin.delete(f"/webhooks/{created['id']}")).status_code == 204
    assert (await admin.get("/webhooks")).json()["items"] == []
    assert (await admin.delete(f"/webhooks/{created['id']}")).status_code == 404
    rows = await session.scalars(
        select(AuditLog.action, AuditLog.reason_source)
        .where(AuditLog.entity_id == uuid.UUID(created["id"]))
        .order_by(AuditLog.ts)
    )
    assert list(rows) == ["webhook_subscription.created", "webhook_subscription.deleted"]


async def test_webhooks_are_per_org_another_org_gets_403(
    session: AsyncSession, world: World, client_for: ClientFor
) -> None:
    a_hook = await subscribe(session, client_for, world, "a.ADMIN")
    s_admin = await client_for(world.users["s.ADMIN"])
    assert (await s_admin.get("/webhooks")).json()["items"] == []
    r = await s_admin.delete(f"/webhooks/{a_hook['id']}")
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"
    assert await session.get(WebhookSubscription, uuid.UUID(str(a_hook["id"]))) is not None


async def test_only_an_org_admin_manages_webhooks(world: World, client_for: ClientFor) -> None:
    manager = await client_for(world.users["a.STORE_MANAGER"])  # has every hospital capability
    assert (await manager.get("/webhooks")).status_code == 403
    assert (await manager.post("/webhooks", json=HOOK)).status_code == 403
    assert (await manager.delete(f"/webhooks/{uuid.uuid4()}")).status_code == 403
    assert (await (await client_for()).get("/webhooks")).status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {**HOOK, "event_types": []},
        {**HOOK, "event_types": ["shortage.deleted"]},
        {**HOOK, "url": "ftp://hooks.example.test/x"},
        {**HOOK, "url": "not a url"},
        {**HOOK, "url": "https://h.test/" + "x" * 2048},
    ],
)
async def test_invalid_subscriptions_are_refused(
    world: World, client_for: ClientFor, body: dict[str, object]
) -> None:
    r = await (await client_for(world.users["a.ADMIN"])).post("/webhooks", json=body)
    assert r.status_code == 422
    assert r.json()["code"] == "schema_error"


# --- deliveries --------------------------------------------------------------------------------


async def test_an_event_goes_only_to_subscriptions_of_addressed_orgs_and_wanted_types(
    session: AsyncSession, redis: Redis, world: World, client_for: ClientFor
) -> None:
    a_hook = await subscribe(session, client_for, world, "a.ADMIN")
    s_hook = await subscribe(
        session, client_for, world, "s.ADMIN", url="https://supplier.example.test/hook"
    )
    a_other_type = await subscribe(
        session,
        client_for,
        world,
        "a.ADMIN",
        url="https://a.example.test/requests",
        event_types=[EventType.SOURCE_REQUEST_CREATED],
    )
    event = await events.emit(session, STATUS, [world.hospital_a.id], {"shortage_id": "x"})
    await events.publish_pending(session, redis, now=T0)

    receiver = Receiver(200)
    async with receiver.client() as http:
        assert await webhooks.deliver_due(session, http, now=T0) == 1
    assert [str(r.url) for r in receiver.requests] == [a_hook["url"]]
    (delivered,) = await deliveries(session, a_hook["id"])
    assert (delivered.event_id, delivered.status, delivered.response_code) == (
        event.id,
        DeliveryStatus.DELIVERED,
        200,
    )
    assert await deliveries(session, s_hook["id"]) == []
    assert await deliveries(session, a_other_type["id"]) == []


async def test_the_signature_verifies_with_the_subscription_secret(
    session: AsyncSession, redis: Redis, world: World, client_for: ClientFor
) -> None:
    hook = await subscribe(session, client_for, world, "a.ADMIN")
    other = await subscribe(
        session, client_for, world, "a.ADMIN", url="https://second.example.test/hook"
    )
    event = await events.emit(session, STATUS, [world.hospital_a.id], {"shortage_id": "x"})
    await events.publish_pending(session, redis, now=T0)

    receiver = Receiver(204)
    async with receiver.client() as http:
        await webhooks.deliver_due(session, http, now=T0)
    by_secret = {}
    for request in receiver.requests:
        body, header = request.content, request.headers[rules.SIGNATURE_HEADER]
        assert header.startswith("sha256=")
        assert request.headers["content-type"] == "application/json"
        envelope = json.loads(body)
        assert envelope["id"] == str(event.id)  # the receiver's idempotency key
        assert set(envelope) == {"id", "type", "occurred_at", "org_ids", "data"}
        for sub in (hook, other):
            if rules.verify(str(sub["secret"]), body, header):
                by_secret[sub["id"]] = request
    # Each request verifies with its own subscription's secret, and only with that one.
    assert set(by_secret) == {hook["id"], other["id"]}
    assert len(receiver.requests) == 2


async def test_failed_deliveries_retry_on_the_schedule_for_24_hours_then_fail(
    session: AsyncSession, redis: Redis, world: World, client_for: ClientFor
) -> None:
    hook = await subscribe(session, client_for, world, "a.ADMIN")
    await events.emit(session, STATUS, [world.hospital_a.id], {"shortage_id": "x"})
    await events.publish_pending(session, redis, now=T0)

    receiver = Receiver(500, None, 503)  # then 503 for ever
    async with receiver.client() as http:
        now, gaps = T0, []
        while True:
            assert await webhooks.deliver_due(session, http, now=now - timedelta(seconds=1)) == 0
            assert await webhooks.deliver_due(session, http, now=now) == 1
            pending = [d for d in await deliveries(session, hook["id"]) if d.status == "PENDING"]
            if not pending:
                break
            gaps.append(pending[0].next_attempt_at - now)
            now = pending[0].next_attempt_at  # the frozen clock jumps to the next attempt

    minutes = [g / timedelta(minutes=1) for g in gaps]
    assert minutes[:8] == [1, 2, 4, 8, 16, 32, 60, 60]
    assert set(minutes[6:]) == {60}
    rows = await deliveries(session, hook["id"])
    assert [d.attempt for d in rows] == list(range(1, len(rows) + 1))
    assert rows[0].response_code == 500
    assert rows[1].response_code is None  # no response at all
    assert {d.status for d in rows[:-1]} == {DeliveryStatus.RETRY_SCHEDULED}
    assert rows[-1].status == DeliveryStatus.FAILED
    # Every attempt fell inside the 24 hours after publishing; the next would not have.
    assert rows[-1].next_attempt_at <= T0 + timedelta(hours=24)
    assert rows[-1].next_attempt_at + timedelta(hours=1) > T0 + timedelta(hours=24)
    # Attempt 7 is 63 min in, then hourly: attempt 29 at 23 h 03 min, attempt 30 too late.
    assert len(rows) == len(receiver.requests) == 29


async def test_a_retry_that_succeeds_ends_the_schedule(
    session: AsyncSession, redis: Redis, world: World, client_for: ClientFor
) -> None:
    hook = await subscribe(session, client_for, world, "a.ADMIN")
    await events.emit(session, STATUS, [world.hospital_a.id], {"shortage_id": "x"})
    await events.publish_pending(session, redis, now=T0)
    receiver = Receiver(500, 200)
    async with receiver.client() as http:
        await webhooks.deliver_due(session, http, now=T0)
        await webhooks.deliver_due(session, http, now=T0 + timedelta(minutes=1))
        assert await webhooks.deliver_due(session, http, now=T0 + timedelta(days=2)) == 0
    statuses = [(d.attempt, d.status) for d in await deliveries(session, hook["id"])]
    assert statuses == [(1, "RETRY_SCHEDULED"), (2, "DELIVERED")]


async def test_deleting_a_subscription_stops_its_deliveries(
    session: AsyncSession, redis: Redis, world: World, client_for: ClientFor
) -> None:
    hook = await subscribe(session, client_for, world, "a.ADMIN")
    await events.emit(session, STATUS, [world.hospital_a.id], {"shortage_id": "x"})
    await events.publish_pending(session, redis, now=T0)
    admin = await client_for(world.users["a.ADMIN"])
    assert (await admin.delete(f"/webhooks/{hook['id']}")).status_code == 204
    receiver = Receiver(200)
    async with receiver.client() as http:
        assert await webhooks.deliver_due(session, http, now=T0) == 0
    assert receiver.requests == []
    assert await deliveries(session, hook["id"]) == []
    assert (await session.scalar(select(EventOutbox).limit(1))) is not None
