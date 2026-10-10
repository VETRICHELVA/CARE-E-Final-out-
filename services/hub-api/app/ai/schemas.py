"""What the copilot's read-only tools see (S13). Each view is the matching user endpoint's
schema (same redaction: no hospital costs, no other org's batches), sometimes with a few
names and ids added so the AI can follow a shortage to its requests and shipments."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.fulfillment import ShipmentStatus
from app.domain.shortage import Status
from app.shortages.schemas import ShortageOut
from app.source_requests.schemas import SourceRequestOut


class AiRecommendationRef(BaseModel):
    id: uuid.UUID
    type: str
    status: str
    created_at: datetime


class AiShipmentRef(BaseModel):
    id: uuid.UUID
    status: ShipmentStatus
    qty: int
    from_org_name: str


class AiShortageRef(BaseModel):
    id: uuid.UUID
    status: Status
    qty_required: int
    shortfall: int
    created_at: datetime


class AiShortageOut(ShortageOut):
    """GET /shortages/{id} plus what hangs off it, as the requester's org sees it."""

    product_code: str
    product_name: str
    facility_name: str
    source_requests: list[SourceRequestOut] = Field(
        description="As GET /source-requests?direction=outgoing&shortage_id= (newest first)."
    )
    recommendations: list[AiRecommendationRef] = Field(description="Newest first.")
    shipments: list[AiShipmentRef] = Field(description="Newest first.")
    residual_shortages: list[AiShortageRef] = Field(
        description="Shortages opened for what a short delivery left (parent_shortage_id)."
    )


class AiColdChainOut(BaseModel):
    """Cold-chain record of a shipment. S15 adds excursion events; until then `events` is
    empty and `has_open_excursion` false (business-rules.md §11)."""

    shipment_id: uuid.UUID
    requires_cold_chain: bool
    device_id: str | None
    reading_count: int
    first_reading_at: datetime | None
    last_reading_at: datetime | None
    min_temp_c: float | None
    max_temp_c: float | None
    has_open_excursion: bool
    events: list[dict[str, object]]


class ProductMatchOut(BaseModel):
    """One catalog product the search phrase may name (S17), with how well it scored."""

    product_id: uuid.UUID
    code: str
    name: str
    category: str
    unit: str
    requires_cold_chain: bool
    default_min_shelf_life_days: int
    score: float = Field(description="0-1; 1.0 = the phrase is the name, code or a synonym.")
    matched_on: str = Field(description="The name, code or synonym that scored best.")


class ProductSearchOut(BaseModel):
    """Best first; products scoring under the search's minimum are left out. The search ranks
    and never picks: callers must ask the user when scores are close (apps-ai-iot.md)."""

    q: str
    items: list[ProductMatchOut]
