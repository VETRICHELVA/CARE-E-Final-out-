import uuid
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db import NulFreeStr
from app.domain.fulfillment import StopType

MAX_SHIPMENTS = 25


class _PlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    driver_id: uuid.UUID
    shipment_ids: list[uuid.UUID] = Field(
        min_length=1,
        max_length=MAX_SHIPMENTS,
        description="Unassigned (CREATED) shipments the caller's org may see; repeats count once.",
    )
    timezone: str = Field(
        default="UTC",
        max_length=64,
        description="IANA time zone for the times in the reasons, e.g. Asia/Kolkata.",
    )

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"Unknown time zone {value!r}.") from e
        return value


class OptimizeIn(_PlanIn):
    vehicle_id: uuid.UUID | None = Field(
        default=None,
        description="Optional: with a vehicle without cold chain, cold-chain shipments are "
        "reported infeasible.",
    )


class ApplyIn(_PlanIn):
    vehicle_id: uuid.UUID
    reason: NulFreeStr | None = None


class RouteStopOut(BaseModel):
    seq: int = Field(description="1-based position in the route.")
    shipment_id: uuid.UUID
    type: StopType
    place: str
    lat: float
    lng: float
    eta: datetime = Field(description="Planned arrival at the stop.")


class InfeasibleOut(BaseModel):
    shipment_id: uuid.UUID
    reason: str = Field(description="Why the shipment cannot be in this route, in plain words.")


class RoutePlanOut(BaseModel):
    """A stop order for one driver: every pickup before its drop, every drop by its
    shortage's `required_by`, every cold-chain shipment within the cold-chain ride limit.
    Shipments that cannot fit are in `infeasible`, never left out silently."""

    driver_id: uuid.UUID
    vehicle_id: uuid.UUID | None
    stops: list[RouteStopOut]
    infeasible: list[InfeasibleOut]


class RouteApplyOut(RoutePlanOut):
    assigned_shipment_ids: list[uuid.UUID] = Field(
        description="The feasible shipments, now ASSIGNED to the driver, in pickup order."
    )
