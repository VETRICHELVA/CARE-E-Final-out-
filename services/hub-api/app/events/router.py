import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Response
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy import select

from app.auth.capabilities import RoleName
from app.auth.deps import CurrentUser, StreamUser, access_claims, require_role
from app.auth.models import User
from app.auth.service import get_redis, issue_stream_ticket
from app.db import SessionDep
from app.errors import AppError
from app.events import service, webhooks
from app.events.models import WebhookSubscription
from app.events.schemas import StreamTicketOut, WebhookCreated, WebhookIn, WebhookOut
from app.pagination import Cursor, Limit, Page, paginate
from app.shortages.schemas import ReasonIn

router = APIRouter(tags=["events"])

# api-and-events.md (S07): webhook subscriptions are managed by an org admin.
OrgAdmin = Annotated[User, Depends(require_role(RoleName.ADMIN))]
RedisDep = Annotated[Redis, Depends(get_redis)]

STREAM_DOC = """Server-sent events for the caller's org: only events whose `org_ids` include it.

Each message is `id: <seq>` and `data: <envelope>`, where the envelope is
`{id, type, occurred_at, org_ids, data}` (api-and-events.md, Events). A comment line
(`: heartbeat`) is sent after 15 seconds without events. Reconnect with the last `id` as the
`Last-Event-ID` header or `last_event_id` query parameter to replay what was missed; if too
much was missed, the stream sends `event: reset` instead, and the client should refetch.
The stream closes after 15 minutes; reconnect with a new ticket."""


@router.post("/events/ticket")
async def create_stream_ticket(
    user: CurrentUser, claims: Annotated[dict[str, Any], Depends(access_claims)]
) -> StreamTicketOut:
    """A 60-second ticket that opens GET /events/stream as the caller (`?ticket=`), because
    a browser EventSource cannot send an Authorization header."""
    del user  # resolving it checked the user is active and the session live
    ticket, expires_at = issue_stream_ticket(claims)
    return StreamTicketOut(ticket=ticket, expires_at=expires_at)


def _last_id(header: str | None, query: int | None) -> int | None:
    if query is not None:
        return query
    if header is None or not header.strip():
        return None
    if not header.strip().isdigit():
        raise AppError(400, "validation", "Last-Event-ID must be an event id from this stream.")
    return int(header)


@router.get(
    "/events/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "Event stream"}},
    description=STREAM_DOC,
)
async def event_stream(
    user: StreamUser,
    session: SessionDep,
    redis: RedisDep,
    last_event_id_header: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    last_event_id: Annotated[int | None, Query(ge=0)] = None,
) -> StreamingResponse:
    after = _last_id(last_event_id_header, last_event_id)
    events = await service.open_stream(session, redis, user.org_id, after)
    # End the (read-only) transaction so its pooled connection goes back now: the session
    # dependency is closed only after the response, and a stream lasts up to 15 minutes.
    await session.commit()
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/webhooks")
async def list_webhooks(
    user: OrgAdmin, session: SessionDep, limit: Limit = 50, cursor: Cursor = None
) -> Page[WebhookOut]:
    """The caller's org's subscriptions, oldest first. Secrets are never listed."""
    stmt = select(WebhookSubscription).where(WebhookSubscription.org_id == user.org_id)
    rows, next_cursor = await paginate(
        session, stmt, WebhookSubscription.created_at, WebhookSubscription.id, limit, cursor
    )
    return Page[WebhookOut](items=[WebhookOut.of(s) for s in rows], next_cursor=next_cursor)


@router.post("/webhooks", status_code=201)
async def create_webhook(user: OrgAdmin, session: SessionDep, body: WebhookIn) -> WebhookCreated:
    """Subscribe the caller's org: events of `event_types` addressed to it are POSTed to
    `url`, signed in `X-CareE-Signature`. The secret is in this response only."""
    sub = await webhooks.create(session, user, body)
    out = WebhookCreated(**WebhookOut.of(sub).model_dump(), secret=sub.secret)
    await session.commit()
    return out


@router.delete("/webhooks/{webhook_id}", status_code=204)
async def delete_webhook(
    webhook_id: uuid.UUID, user: OrgAdmin, session: SessionDep, body: ReasonIn | None = None
) -> Response:
    """Own org's subscription only (403 otherwise). Its pending deliveries stop."""
    await webhooks.delete(session, user, webhook_id, body.reason if body else None)
    await session.commit()
    return Response(status_code=204)
