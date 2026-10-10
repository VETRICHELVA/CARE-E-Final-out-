import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ReliabilityOut(BaseModel):
    """An org's stored reliability (business-rules.md §12). Components are null while the
    org has no history for them; the score is then the no-history default (70)."""

    org_id: uuid.UUID
    score: int = Field(ge=0, le=100, description="0-100; what matching ranks by.")
    has_history: bool = Field(
        description="False while the score is the no-history default (some component has "
        "no history yet)."
    )
    acceptance_rate: float | None = Field(
        description="Share of answered source requests accepted; for a supplier, purchase "
        "orders acknowledged ÷ (acknowledged + rejected)."
    )
    response_speed: float | None = Field(
        description="max(0, 1 - median(response time ÷ the response limit)); for a supplier, "
        "from purchase order SENT to its first answer."
    )
    median_response_minutes: float | None
    on_time_rate: float | None = Field(
        description="Share of delivered shipments delivered by the shortage's deadline."
    )
    discrepancy_rate: float | None = Field(
        description="Units not accepted ÷ units expected, over reconciled shipments."
    )
    computed_at: datetime | None = Field(description="Null until first computed.")
    credits: int | None = Field(
        description="The org's credit balance (§12, not spendable yet); the caller's own org "
        "only, null for any other org."
    )
