import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.db import NonNegInt4, NulFreeStr
from app.domain.surplus import MatchKind, SurplusStatus


class SurplusCreate(BaseModel):
    """Offer one of the caller's org's batches to the network. `qty` may not exceed the
    batch's transferable now; afterwards the post offers only up to its current
    transferable (CLAUDE.md rule 4)."""

    model_config = ConfigDict(extra="forbid")

    batch_id: uuid.UUID
    qty: NonNegInt4 = Field(gt=0)
    min_price_paise: NonNegInt4 | None = None
    reason: NulFreeStr | None = None


class SurplusOut(BaseModel):
    """The poster's own view of its post."""

    id: uuid.UUID
    org_id: uuid.UUID
    batch_id: uuid.UUID
    product_id: uuid.UUID
    qty: int = Field(description="What was posted.")
    offered_qty: int = Field(
        description="What the network is offered now: qty, never more than the batch's "
        "current hub-computed transferable (0 once the batch has expired).",
    )
    expiry_date: date
    min_price_paise: int | None
    status: SurplusStatus
    matched_org_ids: list[uuid.UUID] = Field(
        description="The orgs it was matched to (each got `surplus.matched`)."
    )
    created_by: uuid.UUID
    created_at: datetime


class Location(BaseModel):
    facility_name: str
    lat: float
    lng: float


class MatchReason(BaseModel):
    """Why the post was matched to the caller's org: its own open shortage of the product,
    or its own forecast stock-out within 14 days."""

    kind: MatchKind
    shortage_id: uuid.UUID | None
    stockout_date: date | None
    matched_at: datetime


class SurplusOfferOut(BaseModel):
    """Another org's post as a matched org sees it: transferable qty, expiry band and
    location only (CLAUDE.md rule 6). No batch, expiry date, price or stock figures."""

    id: uuid.UUID
    org_id: uuid.UUID
    org_name: str
    product_id: uuid.UUID
    offered_qty: int = Field(description="Never more than the batch's current transferable.")
    expiry_band: str = Field(
        description="UNDER_30_DAYS, 30_TO_59_DAYS, 60_TO_89_DAYS or 90_DAYS_OR_MORE."
    )
    location: Location
    status: SurplusStatus
    match: MatchReason
