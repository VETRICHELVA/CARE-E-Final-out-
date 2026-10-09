"""S20: the worker prunes published outbox events past their retention, keeps anything a
webhook still has to deliver, and replay answers an older Last-Event-ID with `reset`."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.conftest import World
from app.domain.events import EventType
from app.domain.webhooks import DeliveryStatus
from app.events import service as events
from app.events.models import EventOutbox, WebhookDelivery, WebhookSubscription

pytestmark = pytest.mark.anyio

T0 = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
STATUS = EventType.SHORTAGE_STATUS_CHANGED
READING = EventType.COLDCHAIN_READING


async def kept(session: AsyncSession) -> set[uuid.UUID]:
    return set(await session.scalars(select(EventOutbox.id)))


async def test_prune_respects_both_retentions_and_unpublished_events(
    session: AsyncSession, redis: Redis, world: World
) -> None:
    a = world.hospital_a.id
    status = await events.emit(session, STATUS, [a], {"n": 1})
    reading = await events.emit(session, READING, [a], {"n": 2})
    await events.publish_pending(session, redis, now=T0)
    unpublished = await events.emit(session, STATUS, [a], {"n": 3})

    # A day and an hour on: only the reading is past its 24 h retention.
    assert await events.prune(session, now=T0 + timedelta(hours=25)) == 1
    assert await kept(session) == {status.id, unpublished.id}
    # Past the 7-day retention: the status event goes too; the unpublished one never does.
    later = T0 + timedelta(hours=settings.event_retention_hours + 1)
    assert await events.prune(session, now=later) == 1
    assert await kept(session) == {unpublished.id}
    assert reading.id not in await kept(session)


async def test_an_event_with_a_pending_delivery_stays_and_finished_deliveries_go(
    session: AsyncSession, redis: Redis, world: World
) -> None:
    a = world.hospital_a.id
    sub = WebhookSubscription(org_id=a, url="https://h.example.test/x", secret="s" * 32,
                              event_types=[STATUS])  # fmt: skip
    session.add(sub)
    await session.flush()
    pending = await events.emit(session, STATUS, [a], {"n": 1})
    done = await events.emit(session, STATUS, [a], {"n": 2})
    await events.publish_pending(session, redis, now=T0)  # schedules one delivery each
    for delivery in await session.scalars(select(WebhookDelivery)):
        if delivery.event_id == done.id:
            delivery.status = DeliveryStatus.DELIVERED
    await session.flush()

    assert await events.prune(session, now=T0 + timedelta(days=30)) == 1
    assert await kept(session) == {pending.id}
    left = list(await session.scalars(select(WebhookDelivery.event_id)))
    assert left == [pending.id]


async def test_replay_after_pruned_events_is_a_reset(
    session: AsyncSession, redis: Redis, world: World
) -> None:
    a = world.hospital_a.id
    for n in range(3):
        await events.emit(session, STATUS, [a], {"n": n})
    await events.publish_pending(session, redis, now=T0)
    seqs = sorted(s for s in await session.scalars(select(EventOutbox.seq)) if s is not None)
    first, last = seqs[0], seqs[-1]
    replayed, reset = await events.missed(session, a, first - 1)
    assert ([s for s, _ in replayed], reset) == (seqs, False)
    await events.prune(session, now=T0 + timedelta(days=30))
    # Everything up to `last` is gone: a client that saw `first` cannot be replayed exactly.
    assert await events.missed(session, a, first) == ([], True)
    # A client that saw the newest event has missed nothing.
    assert await events.missed(session, a, last) == ([], False)
