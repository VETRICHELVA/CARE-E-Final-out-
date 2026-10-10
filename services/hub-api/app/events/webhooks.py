"""Webhook subscriptions and deliveries (api-and-events.md, Webhooks).

A subscription receives only events addressed to its own org (`org_ids`), of the types it
chose. The publisher schedules attempt 1 when it publishes an event; `deliver_due` (arq
worker) POSTs the envelope signed with the subscription secret and, on failure, schedules
the next attempt (1 min doubling, capped at 1 h) until 24 h after publishing, then FAILED."""

import asyncio
import json
import logging
import secrets
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import net
from app.audit import service as audit
from app.auth.models import User
from app.config import settings
from app.domain import webhooks as rules
from app.domain.webhooks import SIGNATURE_HEADER, DeliveryStatus
from app.errors import AppError
from app.events.models import EventOutbox, WebhookDelivery, WebhookSubscription
from app.events.schemas import WebhookIn

log = logging.getLogger("app.webhooks")

ENTITY = "webhook_subscription"
DELIVERY_BATCH = 50
DELIVERY_TIMEOUT_SECONDS = 10.0


# --- subscriptions -----------------------------------------------------------------------------


def check_url(url: str) -> None:
    """S20: outside dev a webhook must be https; and unless a dev hub allows it
    (WEBHOOK_ALLOW_PRIVATE_TARGETS), its host may not be a local name or an internal IP. Names
    are resolved and checked again before every delivery, which connects to the checked
    address (`net.check_target`, `net.pin`)."""
    if not settings.is_dev and urlsplit(url).scheme != "https":
        raise AppError(
            400, "validation", "Webhook URLs must use https.", {"reason": "https_required"}
        )
    if not net.private_targets_allowed() and (refusal := net.literal_target_refusal(url)):
        raise AppError(
            400,
            "validation",
            f"Webhooks cannot target internal addresses: {refusal}",
            {"reason": "webhook_target_not_allowed"},
        )


async def create(session: AsyncSession, user: User, body: WebhookIn) -> WebhookSubscription:
    check_url(str(body.url))
    sub = WebhookSubscription(
        org_id=user.org_id,
        url=str(body.url),
        secret=secrets.token_urlsafe(32),
        event_types=sorted(set(body.event_types)),
    )
    session.add(sub)
    await session.flush()
    await session.refresh(sub)  # created_at is set by the database
    after = {"url": sub.url, "event_types": sub.event_types}  # never the secret
    await audit.record(session, user, ENTITY, sub.id, f"{ENTITY}.created", None, after, body.reason)
    return sub


async def own(session: AsyncSession, user: User, sub_id: uuid.UUID) -> WebhookSubscription:
    sub = await session.get(WebhookSubscription, sub_id)
    if sub is None:
        raise AppError(404, "not_found", "Webhook subscription not found.")
    if sub.org_id != user.org_id:
        raise AppError(403, "forbidden", "This webhook belongs to another organization.")
    return sub


async def delete(session: AsyncSession, user: User, sub_id: uuid.UUID, reason: str | None) -> None:
    """Deleting a subscription stops its deliveries; its delivery rows go with it."""
    sub = await own(session, user, sub_id)
    before = {"url": sub.url, "event_types": sub.event_types}
    await audit.record(session, user, ENTITY, sub.id, f"{ENTITY}.deleted", before, None, reason)
    await session.delete(sub)
    await session.flush()


# --- deliveries --------------------------------------------------------------------------------


async def schedule(session: AsyncSession, events: Sequence[EventOutbox], now: datetime) -> None:
    """Attempt 1 for each (event, subscription of an addressed org that wants this type)."""
    orgs = {org_id for e in events for org_id in e.org_ids}
    if not orgs:
        return
    subs = list(
        await session.scalars(
            select(WebhookSubscription).where(WebhookSubscription.org_id.in_(orgs))
        )
    )
    for event in events:
        for sub in subs:
            if sub.org_id in event.org_ids and event.event_type in sub.event_types:
                session.add(
                    WebhookDelivery(
                        subscription_id=sub.id,
                        event_id=event.id,
                        attempt=1,
                        status=DeliveryStatus.PENDING,
                        next_attempt_at=now,
                    )
                )


def body_of(event: EventOutbox) -> bytes:
    """The raw body: the event envelope. The signature covers exactly these bytes."""
    return json.dumps(event.payload, separators=(",", ":")).encode()


async def _post(
    http: httpx.AsyncClient,
    sub: WebhookSubscription,
    event: EventOutbox,
    resolver: net.Resolver | None = None,
) -> int | None:
    """The response status, or None if there was none (connection error, timeout, or a
    target that now resolves to an internal address, S20). The host is resolved once, every
    address checked, and the request connects to the checked address (`net.pin`)."""
    body = body_of(event)
    headers = {
        "Content-Type": "application/json",
        SIGNATURE_HEADER: rules.signature(sub.secret, body),
    }
    request = http.build_request(
        "POST", sub.url, content=body, headers=headers, timeout=DELIVERY_TIMEOUT_SECONDS
    )
    if not net.private_targets_allowed():
        target = await net.check_target(sub.url, resolver or net.resolve)
        if target.ip is None:
            log.warning(
                "webhook target refused",
                extra={"subscription_id": str(sub.id), "error": target.refusal},
            )
            return None
        net.pin(request, target.ip)
    try:
        response = await http.send(request)
    except httpx.HTTPError as e:
        log.warning(
            "webhook delivery failed", extra={"subscription_id": str(sub.id), "error": str(e)}
        )
        return None
    return response.status_code


async def deliver_due(
    session: AsyncSession,
    http: httpx.AsyncClient,
    *,
    now: datetime | None = None,
    resolver: net.Resolver | None = None,
) -> int:
    """Attempt every PENDING delivery due by `now` (rows locked, SKIP LOCKED for parallel
    workers), record each result and schedule retries; returns how many were attempted."""
    now = now or datetime.now(UTC)
    rows = (
        await session.execute(
            select(WebhookDelivery, WebhookSubscription, EventOutbox)
            .join(WebhookSubscription, WebhookSubscription.id == WebhookDelivery.subscription_id)
            .join(EventOutbox, EventOutbox.id == WebhookDelivery.event_id)
            .where(
                WebhookDelivery.status == DeliveryStatus.PENDING,
                WebhookDelivery.next_attempt_at <= now,
            )
            .order_by(WebhookDelivery.next_attempt_at, WebhookDelivery.id)
            .limit(DELIVERY_BATCH)
            .with_for_update(of=WebhookDelivery, skip_locked=True)
        )
    ).all()
    codes = await asyncio.gather(*(_post(http, sub, event, resolver) for _, sub, event in rows))
    for (delivery, _, event), code in zip(rows, codes, strict=True):
        delivery.response_code = code
        if code is not None and 200 <= code < 300:
            delivery.status = DeliveryStatus.DELIVERED
            continue
        assert event.published_at is not None  # deliveries are scheduled when it is published
        at = rules.next_attempt_at(event.published_at, delivery.attempt, now)
        if at is None:
            delivery.status = DeliveryStatus.FAILED
            continue
        delivery.status = DeliveryStatus.RETRY_SCHEDULED
        session.add(
            WebhookDelivery(
                subscription_id=delivery.subscription_id,
                event_id=delivery.event_id,
                attempt=delivery.attempt + 1,
                status=DeliveryStatus.PENDING,
                next_attempt_at=at,
            )
        )
    await session.commit()
    return len(rows)
