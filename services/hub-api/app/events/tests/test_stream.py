"""GET /events/stream over a real ASGI stream: org isolation, Last-Event-ID replay, heartbeat,
and the stream ticket. Events are emitted in the test transaction, then published to Redis
by the publisher exactly as the worker would."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import service as auth_service
from app.catalog.models import Product
from app.config import settings
from app.conftest import ClientFor, World
from app.domain.events import EventType
from app.events import service as events
from app.events.models import EventOutbox
from app.events.tests.conftest import StreamFor, bearer
from app.shortages.models import Priority
from app.source_requests.tests.conftest import facility_of, seed_scenario1

pytestmark = pytest.mark.anyio

STATUS = EventType.SHORTAGE_STATUS_CHANGED


async def emit(session: AsyncSession, *orgs: uuid.UUID, n: int = 0) -> EventOutbox:
    data = {"shortage_id": uuid.uuid4(), "from": "OPEN", "to": "MATCHING", "n": n}
    return await events.emit(session, STATUS, orgs, data)


async def test_org_b_never_receives_an_event_addressed_only_to_org_a(
    session: AsyncSession, redis: Redis, world: World, stream_for: StreamFor
) -> None:
    a, b = world.hospital_a.id, world.hospital_b.id
    as_a = await bearer(session, world.users["a.STORE_MANAGER"])
    as_b = await bearer(session, world.users["b.STORE_MANAGER"])
    async with stream_for(as_a) as sse_a, stream_for(as_b) as sse_b:
        assert (sse_a.status, sse_b.status) == (200, 200)
        assert (await sse_a.next()).comment == "connected"
        assert (await sse_b.next()).comment == "connected"
        # An open stream holds no database transaction (or pooled connection).
        assert not session.in_transaction()
        only_a = await emit(session, a)
        only_b = await emit(session, b)
        both = await emit(session, a, b)
        assert await events.publish_pending(session, redis) == 3

        got_b = await sse_b.events_until(lambda f: f.envelope["id"] == str(both.id))
        got_a = await sse_a.events_until(lambda f: f.envelope["id"] == str(both.id))

    # B's last event was published after A's private one, so it would have arrived by now.
    assert [f.envelope["id"] for f in got_b] == [str(only_b.id), str(both.id)]
    assert [f.envelope["id"] for f in got_a] == [str(only_a.id), str(both.id)]
    assert all(str(b) in f.envelope["org_ids"] for f in got_b)
    assert only_a.seq is not None and str(only_a.seq) not in [f.id for f in got_b]
    # The envelope is the spec's: {id, type, occurred_at, org_ids, data}.
    assert set(got_a[0].envelope) == {"id", "type", "occurred_at", "org_ids", "data"}
    assert got_a[0].envelope["type"] == "shortage.status_changed"
    assert got_a[0].id == str(only_a.seq)

    # Replay is filtered the same way: B resuming from the start sees only its own two.
    async with stream_for(as_b, last_event_id=0) as sse_b:
        replayed = await sse_b.events_until(lambda f: f.envelope["id"] == str(both.id))
    assert [f.envelope["id"] for f in replayed][-2:] == [str(only_b.id), str(both.id)]
    assert str(only_a.id) not in [f.envelope["id"] for f in replayed]


async def test_a_real_shortage_reaches_only_the_requester_and_the_asked_source(
    session: AsyncSession,
    redis: Redis,
    world: World,
    products: dict[str, Product],
    client_for: ClientFor,
    stream_for: StreamFor,
) -> None:
    """Scenario 1 step 1-2 through the API: A sees its shortage move and the request to B;
    B sees only the request; Supplier S, not part of the shortage, sees nothing."""
    now = datetime.now(UTC)
    ska = products["SURG-KIT-A"]
    await seed_scenario1(session, world.hospital_a, world.hospital_b, ska, now)
    facility = await facility_of(session, world.hospital_a)
    a = await client_for(world.users["a.REQUESTER"])
    as_a = await bearer(session, world.users["a.REQUESTER"])
    as_b = await bearer(session, world.users["b.STORE_MANAGER"])
    as_s = await bearer(session, world.users["s.SUPPLIER_DESK"])
    async with stream_for(as_a) as sse_a, stream_for(as_b) as sse_b, stream_for(as_s) as sse_s:
        for sse in (sse_a, sse_b, sse_s):
            assert (await sse.next()).comment == "connected"
        r = await a.post(
            "/shortages",
            json={
                "facility_id": str(facility.id),
                "product_id": str(ska.id),
                "qty_required": 1000,
                "qty_local_usable": 150,
                "required_by": (now + timedelta(hours=72)).isoformat(),
                "priority": Priority.CRITICAL,
            },
        )
        assert r.status_code == 201, r.text
        shortage_id = r.json()["id"]
        # A marker for S: if anything of the shortage leaked to S, it would come before this.
        marker = await emit(session, world.supplier.id)
        await events.publish_pending(session, redis)

        types_a = [f.envelope["type"] for f in await sse_a.events_until(_request_created)]
        got_b = await sse_b.events_until(_request_created)
        got_s = await sse_s.events_until(lambda f: f.envelope["id"] == str(marker.id))

    assert types_a == ["shortage.status_changed", "source_request.created"]
    assert [f.envelope["type"] for f in got_b] == ["source_request.created"]
    data = got_b[0].envelope["data"]
    assert data["shortage_id"] == shortage_id
    assert data["qty"] == 850
    assert set(data) == {"source_request_id", "shortage_id", "product_id", "qty", "deadline"}
    assert [f.envelope["id"] for f in got_s] == [str(marker.id)]


def _request_created(frame: object) -> bool:
    return getattr(frame, "envelope", {}).get("type") == "source_request.created"


async def test_last_event_id_replays_missed_events_then_continues_live(
    session: AsyncSession, redis: Redis, world: World, stream_for: StreamFor
) -> None:
    a = world.hospital_a.id
    as_a = await bearer(session, world.users["a.STORE_MANAGER"])
    seen, missed1, missed2 = [await emit(session, a, n=n) for n in range(3)]
    await events.publish_pending(session, redis)
    assert seen.seq is not None and missed2.seq is not None

    # The browser's EventSource resends the last id as the Last-Event-ID header.
    async with stream_for({**as_a, "Last-Event-ID": str(seen.seq)}) as sse:
        replayed = [await sse.next_event(), await sse.next_event()]
        live = await emit(session, a, n=3)
        await events.publish_pending(session, redis)
        after = await sse.next_event()
    assert [f.envelope["id"] for f in replayed] == [str(missed1.id), str(missed2.id)]
    assert [f.id for f in replayed] == [str(missed1.seq), str(missed2.seq)]
    assert after.envelope["id"] == str(live.id)

    # A client reconnecting by itself passes it as `last_event_id`; nothing new means nothing.
    async with stream_for(as_a, last_event_id=live.seq) as sse:
        assert (await sse.next()).comment == "connected"
        with pytest.raises(TimeoutError):
            await sse.next_event(wait=0.3)


async def test_too_many_missed_events_send_reset_instead(
    session: AsyncSession,
    redis: Redis,
    world: World,
    stream_for: StreamFor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(events, "REPLAY_LIMIT", 2)
    first = await emit(session, world.hospital_a.id)
    for n in range(3):
        await emit(session, world.hospital_a.id, n=n)
    await events.publish_pending(session, redis)
    as_a = await bearer(session, world.users["a.STORE_MANAGER"])
    async with stream_for(as_a, last_event_id=first.seq) as sse:
        assert (await sse.next()).comment == "connected"
        frame = await sse.next()
    assert (frame.event, frame.data) == ("reset", "{}")


async def test_heartbeat_and_stream_lifetime(
    session: AsyncSession, world: World, stream_for: StreamFor, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert events.HEARTBEAT_SECONDS == 15
    assert timedelta(seconds=events.MAX_STREAM_SECONDS) == auth_service.ACCESS_TTL
    monkeypatch.setattr(events, "HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(events, "MAX_STREAM_SECONDS", 0.3)
    as_a = await bearer(session, world.users["a.STORE_MANAGER"])
    async with stream_for(as_a) as sse:
        assert (await sse.next()).comment == "connected"
        assert (await sse.next(wait=1)).comment == "heartbeat"
        with pytest.raises(EOFError):  # closes on its own; the client reconnects
            while True:
                await sse.next(wait=1)


async def test_a_ticket_opens_the_stream_and_nothing_else(
    session: AsyncSession, world: World, client_for: ClientFor, stream_for: StreamFor
) -> None:
    user = world.users["b.STORE_MANAGER"]
    client = await client_for(user)
    r = await client.post("/events/ticket")
    assert r.status_code == 200, r.text
    ticket = r.json()["ticket"]
    expires_at = datetime.fromisoformat(r.json()["expires_at"])
    assert timedelta(0) < expires_at - datetime.now(UTC) <= timedelta(seconds=60)

    async with stream_for({}, ticket=ticket) as sse:
        assert sse.status == 200
        assert (await sse.next()).comment == "connected"

    # A ticket is not an access token, and an access token is not a ticket.
    as_ticket = await client_for()
    r = await as_ticket.get("/auth/me", headers={"Authorization": f"Bearer {ticket}"})
    assert r.status_code == 401
    access = (await bearer(session, user))["Authorization"].removeprefix("Bearer ")
    async with stream_for({}, ticket=access) as sse:
        assert sse.status == 401
        assert b"unauthenticated" in sse.body


async def test_the_stream_refuses_missing_expired_and_revoked_credentials(
    session: AsyncSession, world: World, client_for: ClientFor, stream_for: StreamFor
) -> None:
    async with stream_for({}) as sse:
        assert sse.status == 401
    assert (await (await client_for()).post("/events/ticket")).status_code == 401

    user = world.users["a.STORE_MANAGER"]
    past = datetime.now(UTC) - timedelta(minutes=5)
    claims = {"sub": str(user.id), "sid": str(uuid.uuid4()), "aud": "events.stream"}
    expired = jwt.encode({**claims, "exp": past}, settings.jwt_secret, algorithm="HS256")
    async with stream_for({}, ticket=expired) as sse:
        assert sse.status == 401
        assert b"Stream ticket expired." in sse.body

    # A ticket whose session is not live (e.g. signed out) is refused like an access token.
    live = jwt.encode(
        {**claims, "exp": datetime.now(UTC) + timedelta(minutes=1)},
        settings.jwt_secret,
        algorithm="HS256",
    )
    async with stream_for({}, ticket=live) as sse:
        assert sse.status == 401


async def test_a_malformed_last_event_id_is_a_400(
    session: AsyncSession, world: World, stream_for: StreamFor
) -> None:
    as_a = await bearer(session, world.users["a.STORE_MANAGER"])
    async with stream_for({**as_a, "Last-Event-ID": "abc"}) as sse:
        assert sse.status == 400
        assert b'"code":"validation"' in sse.body
