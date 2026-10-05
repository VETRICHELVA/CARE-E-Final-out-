import uuid
from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.db import NonNegInt4, NulFree, NulFreeStr
from app.domain.inventory import batch_transferable
from app.inventory.models import InventoryBatch, VerificationMethod

BatchNo = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64), NulFree
]
ProductCode = Annotated[
    str,
    StringConstraints(strip_whitespace=True, to_upper=True, min_length=1, max_length=32),
    NulFree,
]


class BatchFields(BaseModel):
    """What a client may set on a batch. `extra="forbid"`: sending `transferable`,
    `last_verified_at` or anything else is a 422 `schema_error`."""

    model_config = ConfigDict(extra="forbid")

    batch_no: BatchNo
    on_hand: NonNegInt4
    reserved: NonNegInt4 = 0
    allocated: NonNegInt4 = 0
    safety_stock: NonNegInt4 = 0
    quarantined: NonNegInt4 = 0
    expiry_date: date
    unit_cost_paise: NonNegInt4


class BatchCreate(BatchFields):
    facility_id: uuid.UUID
    product_id: uuid.UUID
    reason: NulFreeStr | None = None


class CsvRow(BatchFields):
    """One CSV import line; `product_code` is matched case-insensitively."""

    product_code: ProductCode


class BatchUpdate(BaseModel):
    """Partial update. Product and facility are fixed; omitted or null fields are unchanged."""

    model_config = ConfigDict(extra="forbid")

    batch_no: BatchNo | None = None
    on_hand: NonNegInt4 | None = None
    reserved: NonNegInt4 | None = None
    allocated: NonNegInt4 | None = None
    safety_stock: NonNegInt4 | None = None
    quarantined: NonNegInt4 | None = None
    expiry_date: date | None = None
    unit_cost_paise: NonNegInt4 | None = None
    reason: NulFreeStr | None = None


class VerifyIn(BaseModel):
    """`method` is required: the hub records only the count method the user stated."""

    model_config = ConfigDict(extra="forbid")

    method: VerificationMethod
    counted_qty: NonNegInt4
    reason: NulFreeStr | None = None


class BatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    facility_id: uuid.UUID
    product_id: uuid.UUID
    batch_no: str
    on_hand: int
    reserved: int
    allocated: int
    safety_stock: int
    quarantined: int
    expiry_date: date
    unit_cost_paise: int
    last_verified_at: datetime | None
    created_at: datetime
    updated_at: datetime
    transferable: int = Field(
        json_schema_extra={"readOnly": True},
        description="Hub-computed (business-rules.md §2); never accepted as input.",
    )

    @classmethod
    def of(cls, batch: InventoryBatch, today: date) -> "BatchOut":
        fields = {f: getattr(batch, f) for f in cls.model_fields if f != "transferable"}
        return cls(**fields, transferable=batch_transferable(batch, today))


class RowError(BaseModel):
    line: int
    message: str


class ImportResult(BaseModel):
    inserted: int
    errors: list[RowError]
