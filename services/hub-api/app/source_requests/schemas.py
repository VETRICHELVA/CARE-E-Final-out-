import uuid
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.source_request import ACTIVE_HOLD, HoldStatus, RequestStatus
from app.shortages.models import Priority, Shortage
from app.source_requests.models import Hold, SourceRequest


class Direction(StrEnum):
    INCOMING = "incoming"  # requests the caller's org is asked to supply
    OUTGOING = "outgoing"  # requests sent for the caller's org's shortages


class HoldOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    batch_id: uuid.UUID
    qty: int
    status: HoldStatus
    expires_at: datetime


class SourceRequestOut(BaseModel):
    """Both orgs see the request itself. Only the source org sees which of its batches are
    held (`holds`) and which of its users answered (`responded_by`); the requester sees
    the total (`held_qty`) and the hold deadline."""

    id: uuid.UUID
    shortage_id: uuid.UUID
    requester_org_id: uuid.UUID
    requester_org_name: str
    source_org_id: uuid.UUID
    source_org_name: str
    product_id: uuid.UUID
    priority: Priority
    required_by: datetime
    qty: int
    status: RequestStatus
    sla_deadline: datetime = Field(description="When the source must accept or decline.")
    hold_expires_at: datetime | None = Field(
        description="When the tentative holds lapse unless the requester decides."
    )
    held_qty: int = Field(description="Units under active (TENTATIVE or FIRM) holds.")
    holds: list[HoldOut] | None = Field(description="Source org only; null for the requester.")
    responded_by: uuid.UUID | None = Field(
        description="The source org user who answered. Source org only; null for the requester."
    )
    responded_at: datetime | None
    decline_reason: str | None
    reason_source: Literal["USER", "SYSTEM"] | None = Field(
        description="On a decline: USER if the source typed a reason, else SYSTEM."
    )
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(
        cls,
        sr: SourceRequest,
        shortage: Shortage,
        names: dict[uuid.UUID, str],
        holds: list[Hold],
        viewer_org_id: uuid.UUID,
    ) -> "SourceRequestOut":
        active = [h for h in holds if h.status in ACTIVE_HOLD]
        tentative = [h.expires_at for h in active if h.status == HoldStatus.TENTATIVE]
        source_view = viewer_org_id == sr.source_org_id
        return cls(
            id=sr.id,
            shortage_id=sr.shortage_id,
            requester_org_id=shortage.org_id,
            requester_org_name=names[shortage.org_id],
            source_org_id=sr.source_org_id,
            source_org_name=names[sr.source_org_id],
            product_id=shortage.product_id,
            priority=Priority(shortage.priority),
            required_by=shortage.required_by,
            qty=sr.qty,
            status=RequestStatus(sr.status),
            sla_deadline=sr.sla_deadline,
            hold_expires_at=min(tentative) if tentative else None,
            held_qty=sum(h.qty for h in active),
            holds=[HoldOut.model_validate(h) for h in holds] if source_view else None,
            responded_by=sr.responded_by if source_view else None,
            responded_at=sr.responded_at,
            decline_reason=sr.decline_reason,
            reason_source=sr.reason_source,  # type: ignore[arg-type]
            created_at=sr.created_at,
            updated_at=sr.updated_at,
        )
