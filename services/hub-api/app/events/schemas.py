import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, HttpUrl, UrlConstraints

from app.db import NulFreeStr
from app.domain.events import EventType
from app.events.models import WebhookSubscription

WebhookUrl = Annotated[HttpUrl, UrlConstraints(max_length=2048)]


class StreamTicketOut(BaseModel):
    ticket: str = Field(
        description="Pass as `?ticket=` to GET /events/stream (EventSource cannot send headers)."
    )
    expires_at: datetime = Field(description="Open the stream before this; it lasts 60 seconds.")


class WebhookIn(BaseModel):
    url: WebhookUrl
    event_types: list[EventType] = Field(min_length=1)
    reason: NulFreeStr | None = None


class WebhookOut(BaseModel):
    id: uuid.UUID
    url: str
    event_types: list[EventType]
    created_at: datetime

    @classmethod
    def of(cls, sub: WebhookSubscription) -> "WebhookOut":
        return cls(
            id=sub.id,
            url=sub.url,
            event_types=[EventType(t) for t in sub.event_types],
            created_at=sub.created_at,
        )


class WebhookCreated(WebhookOut):
    secret: str = Field(
        description="Shown only now. Verify `X-CareE-Signature: sha256=<hex HMAC-SHA256 of the "
        "raw body>` with it."
    )
