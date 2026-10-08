import uuid
from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.db import NonNegInt4, NulFreeStr
from app.domain.reconciliation import Condition, Outcome


class ReceiptIn(BaseModel):
    """What the receiver counted. `expected` is the shipment's qty (read-only; sending it is
    a 422). Invariants (business-rules.md §9), else 400: received ≤ expected and accepted +
    rejected = received."""

    model_config = ConfigDict(extra="forbid")

    received: NonNegInt4
    accepted: NonNegInt4
    rejected: NonNegInt4
    condition: Condition
    inspection_note: NulFreeStr | None = Field(
        default=None,
        description="Required (400) when the shipment has an open cold-chain excursion "
        "(`inspection_note_required` on the shipment).",
    )
    expiry_date: date | None = Field(
        default=None,
        description="The expiry printed on the accepted stock. Required (400) when accepted > 0: "
        "the accepted stock becomes a new batch in the receiver's inventory.",
    )
    batch_no: (
        Annotated[NulFreeStr, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
        | None
    ) = Field(
        default=None,
        description="The new batch's number; `RCV-` and the shipment id's first 8 characters "
        "if left out. A batch number already used for this product at the facility is 409.",
    )
    reason: NulFreeStr | None = None


class ReconciliationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    shortage_id: uuid.UUID
    shipment_id: uuid.UUID
    expected: int
    accepted: int
    discrepancy: int = Field(description="expected - accepted, for this shipment.")
    outcome: Outcome = Field(
        description="The shortage's: CONFIRMED (shortfall accepted in full, RESOLVED) or "
        "PARTIAL (PARTIALLY_RESOLVED, residual opened)."
    )
    residual_shortage_id: uuid.UUID | None
    created_at: datetime


class ReceiptOut(BaseModel):
    """Seen by the receiving org only."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    shipment_id: uuid.UUID
    shortage_id: uuid.UUID
    expected: int
    received: int
    accepted: int
    rejected: int
    condition: Condition
    inspection_note: str | None
    received_by: uuid.UUID
    ts: datetime
    batch_id: uuid.UUID | None = Field(
        description="The receiver's new inventory batch holding the accepted stock."
    )
    reconciliation: ReconciliationOut | None = Field(
        default=None,
        description="Null until every shipment of the shortage has a receipt.",
    )
