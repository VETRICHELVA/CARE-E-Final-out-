import uuid

from pydantic import BaseModel, ConfigDict, Field


class DemandOut(BaseModel):
    """Open network demand for one product the caller's supplier org offers. Aggregated over
    every hospital: no hospital, facility, shortage or count of either (CLAUDE.md rule 6)."""

    model_config = ConfigDict(extra="forbid")

    product_id: uuid.UUID
    open_shortfall_qty: int = Field(
        description="Sum of the hub-computed shortfall of every other org's shortage for this "
        "product that is OPEN, MATCHING or AWAITING_DECISION; 0 when there is none."
    )
