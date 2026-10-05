import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.db import NonNegInt4, NulFreeStr


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    category: str
    unit: str
    requires_cold_chain: bool
    temp_min_c: float | None
    temp_max_c: float | None
    default_min_shelf_life_days: int


class OfferIn(BaseModel):
    """One offer per PUT, keyed by the caller's org and `product_id` (upsert)."""

    model_config = ConfigDict(extra="forbid")

    product_id: uuid.UUID
    unit_price_paise: NonNegInt4
    lead_time_hours: NonNegInt4
    available_qty: NonNegInt4
    reason: NulFreeStr | None = None


class OfferOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    product_id: uuid.UUID
    unit_price_paise: int
    lead_time_hours: int
    available_qty: int
    created_at: datetime
    updated_at: datetime
