import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.recommendation import RecStatus
from app.recommendations.models import Recommendation
from app.shortages.models import SourceType


class RecommendationLineOut(BaseModel):
    """One source of a recommendation as the requester's org sees it. A hospital source's
    landed cost is never shown: it is quantity x that hospital's unit cost + transport + a
    2% handling fee, and transport follows from the two public locations, so any cost figure
    for a hospital line would reveal its unit cost (CLAUDE.md rule 6)."""

    candidate_id: uuid.UUID
    source_org_id: uuid.UUID
    source_org_name: str
    source_type: SourceType
    source_request_id: uuid.UUID | None = Field(description="Hospital sources: the request.")
    qty: int
    eta_hours: float
    shelf_life_days: int | None = Field(
        description="Hospital sources: days of shelf life left at delivery on the held stock."
    )
    unit_price_paise: int | None = Field(description="Supplier sources only.")
    landed_cost_paise: int | None = Field(
        description="Supplier sources only; always null for a hospital source."
    )

    @classmethod
    def of(cls, line: dict[str, Any]) -> "RecommendationLineOut":
        out = cls.model_validate(line)
        if out.source_type == SourceType.HOSPITAL:
            out.landed_cost_paise = None
            out.unit_price_paise = None
        return out


class RecommendationOut(BaseModel):
    id: uuid.UUID
    shortage_id: uuid.UUID
    match_run_id: uuid.UUID
    type: Literal["TRANSFER", "TRANSFER_SPLIT", "BUY"]
    status: RecStatus
    lines: list[RecommendationLineOut]
    alternatives: list[RecommendationLineOut] = Field(
        description="The best BUY for a transfer, or for a BUY the next supplier."
    )
    total_landed_cost_paise: int | None = Field(
        description="BUY only (supplier lines); null whenever a hospital source is involved."
    )
    explanation: str = Field(description="Built by the hub from the stored candidate data.")
    expires_at: datetime
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    reason: str | None = Field(
        description="What the decider typed, or a timer's cause; null if nothing was typed."
    )
    reason_source: Literal["USER", "SYSTEM"] | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, rec: Recommendation) -> "RecommendationOut":
        lines = [RecommendationLineOut.of(x) for x in rec.lines]
        costs = [x.landed_cost_paise for x in lines]
        all_supplier = all(x.source_type == SourceType.SUPPLIER for x in lines)
        return cls(
            id=rec.id,
            shortage_id=rec.shortage_id,
            match_run_id=rec.match_run_id,
            type=rec.type,  # type: ignore[arg-type]
            status=RecStatus(rec.status),
            lines=lines,
            alternatives=[RecommendationLineOut.of(x) for x in rec.alternatives],
            total_landed_cost_paise=(
                sum(c for c in costs if c is not None)
                if all_supplier and None not in costs
                else None
            ),
            explanation=rec.explanation,
            expires_at=rec.expires_at,
            decided_by=rec.decided_by,
            decided_at=rec.decided_at,
            reason=rec.reason,
            reason_source=rec.reason_source,  # type: ignore[arg-type]
            created_at=rec.created_at,
            updated_at=rec.updated_at,
        )


class ApprovalOut(BaseModel):
    recommendation: RecommendationOut
    message: str = Field(description="business-rules.md §13 wording for the approved type.")
    shipment_ids: list[uuid.UUID] = Field(description="Transfers: one per source.")
    purchase_order_id: uuid.UUID | None = Field(description="BUY: the order sent.")
