"""Events (api-and-events.md, Events and Realtime): the outbox, the publisher and the stream.

`emit` writes an EventOutbox row in the caller's transaction, so an event exists only if its
state change commits. The worker's `publish_pending` then gives each row its publish `seq`,
schedules its webhook deliveries and publishes it to one Redis channel per addressed org.
`GET /events/stream` subscribes to the caller's org channel only, so an event never reaches
an org outside its `org_ids`; replay after a reconnect reads the outbox with the same filter."""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Iterable
from datetime import UTC, datetime
from typing import Any

from fastapi.encoders import jsonable_encoder
from redis.asyncio import Redis
from redis.asyncio.client import PubSub
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import ACCESS_TTL
from app.domain.events import EventType
from app.events import webhooks
from app.events.models import PUBLISH_SEQ, EventOutbox

PUBLISH_BATCH = 100
PUBLISH_LOCK = 0x0CA4E5E7  # pg advisory lock key: one publisher at a time keeps `seq` in order
HEARTBEAT_SECONDS = 15.0
# A stream ends when an access token would, so a revoked session stops receiving events
# within the same 15 minutes; the client reconnects with a new ticket and Last-Event-ID.
MAX_STREAM_SECONDS = ACCESS_TTL.total_seconds()
REPLAY_LIMIT = 1000  # more missed events than this: send `reset` and let the client refetch


def channel(org_id: uuid.UUID | str) -> str:
    return f"care-e:events:{org_id}"


async def emit(
    session: AsyncSession,
    event_type: EventType,
    org_ids: Iterable[uuid.UUID],
    data: dict[str, Any],
) -> EventOutbox:
    """Add an event to the outbox in the caller's transaction. Only `org_ids` receive it."""
    orgs = sorted(set(org_ids), key=str)
    if not orgs:
        raise ValueError("An event must be addressed to at least one org.")
    event_id = uuid.uuid4()
    payload = jsonable_encoder(
        {
            "id": event_id,
            "type": event_type,
            "occurred_at": datetime.now(UTC),
            "org_ids": orgs,
            "data": data,
        }
    )
    row = EventOutbox(id=event_id, event_type=event_type, org_ids=orgs, payload=payload)
    session.add(row)
    await session.flush()
    return row


# --- publisher (arq worker) --------------------------------------------------------------------


async def publish_pending(
    session: AsyncSession, redis: Redis, *, now: datetime | None = None
) -> int:
    """Publish every unpublished outbox row, oldest first; returns how many. Each batch is
    one transaction under an advisory lock: rows get consecutive `seq` values in the order
    they are published, Redis gets them before the commit (at least once: a failed commit
    leaves them unpublished, so they go out again on the next run)."""
    total = 0
    while True:
        done = await _publish_batch(session, redis, now or datetime.now(UTC))
        total += done
        if done < PUBLISH_BATCH:
            return total


async def _publish_batch(session: AsyncSession, redis: Redis, now: datetime) -> int:
    if not await session.scalar(select(func.pg_try_advisory_xact_lock(PUBLISH_LOCK))):
        await session.rollback()  # another publisher is running
        return 0
    rows = list(
        await session.scalars(
            select(EventOutbox)
            .where(EventOutbox.published_at.is_(None))
            .order_by(EventOutbox.created_at, EventOutbox.id)
            .limit(PUBLISH_BATCH)
            .with_for_update(skip_locked=True)
        )
    )
    for row in rows:
        row.seq = await session.scalar(select(PUBLISH_SEQ.next_value()))
        row.published_at = now
    await webhooks.schedule(session, rows, now)
    await session.flush()
    for row in rows:
        message = json.dumps({"seq": row.seq, "event": row.payload})
        for org_id in row.org_ids:
            await redis.publish(channel(org_id), message)
    await session.commit()
    return len(rows)


# --- stream ------------------------------------------------------------------------------------


def frame(seq: int, payload: dict[str, Any]) -> str:
    """One server-sent event. `id` is the publish seq, which Last-Event-ID resumes from."""
    return f"id: {seq}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"


async def missed(
    session: AsyncSession, org_id: uuid.UUID, after: int
) -> tuple[list[tuple[int, dict[str, Any]]], bool]:
    """Events for `org_id` published after `after`, in publish order, and whether there were
    too many to replay (then the client gets `reset` instead)."""
    rows = (
        await session.execute(
            select(EventOutbox.seq, EventOutbox.payload)
            .where(EventOutbox.seq > after, EventOutbox.org_ids.contains([org_id]))
            .order_by(EventOutbox.seq)
            .limit(REPLAY_LIMIT + 1)
        )
    ).all()
    if len(rows) > REPLAY_LIMIT:
        return [], True
    return [(int(seq), payload) for seq, payload in rows], False


async def open_stream(
    session: AsyncSession, redis: Redis, org_id: uuid.UUID, last_event_id: int | None
) -> AsyncIterator[str]:
    """Subscribe first, then read what was missed, so nothing published in between is lost;
    live events already replayed are skipped by `seq`. All database reads happen here, before
    the response starts streaming."""
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel(org_id))
    try:
        backlog: list[tuple[int, dict[str, Any]]] = []
        reset = False
        if last_event_id is not None:
            backlog, reset = await missed(session, org_id, last_event_id)
    except BaseException:
        await pubsub.aclose()  # type: ignore[no-untyped-call]
        raise
    return _stream(pubsub, org_id, backlog, reset, None if reset else last_event_id)


async def _stream(
    pubsub: PubSub,
    org_id: uuid.UUID,
    backlog: list[tuple[int, dict[str, Any]]],
    reset: bool,
    last: int | None,
) -> AsyncIterator[str]:
    loop = asyncio.get_running_loop()
    ends_at = loop.time() + MAX_STREAM_SECONDS
    try:
        yield ": connected\n\n"
        if reset:
            yield "event: reset\ndata: {}\n\n"
        for seq, payload in backlog:
            yield frame(seq, payload)
            last = seq
        beat_at = loop.time() + HEARTBEAT_SECONDS
        while (left := ends_at - loop.time()) > 0:
            if loop.time() >= beat_at:
                yield ": heartbeat\n\n"
                beat_at = loop.time() + HEARTBEAT_SECONDS
            # None on a timeout, and also for a subscribe confirmation (ignored message).
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=max(0.0, min(beat_at - loop.time(), left))
            )
            if message is None:
                continue
            body = json.loads(message["data"])
            seq, payload = int(body["seq"]), body["event"]
            if last is not None and seq <= last:
                continue  # already replayed
            if str(org_id) not in payload["org_ids"]:
                continue  # never on this org's channel; checked again all the same
            yield frame(seq, payload)
            last = seq
    finally:
        await pubsub.aclose()  # type: ignore[no-untyped-call]
