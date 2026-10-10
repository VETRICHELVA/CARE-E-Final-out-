import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field


class ForecastDayOut(BaseModel):
    date: date
    predicted_qty: float
    lower: float = Field(description="Lower bound of the 95% prediction interval.")
    upper: float = Field(description="Upper bound of the 95% prediction interval.")


class ReorderOut(BaseModel):
    lead_time_days: int = Field(
        description="The shortest supplier lead time for the product in whole days (rounded "
        "up), or the default (7) when no supplier offers it."
    )
    qty: int = Field(
        description="Forecast lead-time demand + safety stock - usable stock, rounded up, "
        "never below 0."
    )


class ExpiryRiskOut(BaseModel):
    """A batch whose forecast usage before expiry is less than on_hand - safety stock."""

    batch_id: uuid.UUID
    batch_no: str
    expiry_date: date
    on_hand: int
    safety_stock: int
    transferable: int
    usage_before_expiry: float
    excess: int = Field(description="on_hand - safety stock - forecast usage, rounded down.")
    suggested_qty: int = Field(
        description='What "Offer to network" posts: the excess, never more than transferable.'
    )
    surplus_post_id: uuid.UUID | None = Field(
        description="The batch's live (OPEN or MATCHED) surplus post, if any."
    )


class ForecastOut(BaseModel):
    """One product's stored forecast against the caller's org's stock now."""

    product_id: uuid.UUID
    model_version: str = Field(
        description="holt-winters-weekly/1, or moving-average-28/1 under 60 days of history."
    )
    generated_at: datetime
    synthetic_history: bool = Field(
        description="True when the history includes synthetic (seeded) consumption records."
    )
    days: list[ForecastDayOut] = Field(description="The next 30 days, from today.")
    usable_stock: int = Field(
        description="On hand less reserved, allocated, quarantined and active holds, over "
        "unexpired batches (safety stock is usable by its own hospital)."
    )
    safety_stock: int
    stockout_date: date | None = Field(
        description="The first day cumulative forecast use exceeds usable stock; null if it "
        "does not within the stored forecast."
    )
    reorder: ReorderOut | None
    expiry_risks: list[ExpiryRiskOut]


class RunOut(BaseModel):
    org_ids: list[uuid.UUID]
    series: int = Field(description="Hospital x product series forecast.")
    models: dict[str, int] = Field(description="Series per model version.")
