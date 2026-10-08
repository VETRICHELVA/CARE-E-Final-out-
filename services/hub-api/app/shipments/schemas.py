import uuid
from datetime import datetime
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.db import NulFreeStr
from app.domain.fulfillment import RouteProvider, ShipmentStatus, StopType
from app.receiving.schemas import ReceiptOut
from app.shortages.models import Priority


class AssignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    driver_id: uuid.UUID
    vehicle_id: uuid.UUID
    reason: NulFreeStr | None = None


class StatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ShipmentStatus = Field(
        description="The next state: PICKED_UP, IN_TRANSIT or DELIVERED, one step at a time."
    )
    reason: NulFreeStr | None = None


class LocationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lng: float = Field(ge=-180, le=180, allow_inf_nan=False)
    ts: AwareDatetime | None = Field(
        default=None, description="When the phone took the fix; the hub's time if left out."
    )


class LocationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    shipment_id: uuid.UUID
    lat: float
    lng: float
    ts: datetime


class DriverOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    phone: str
    active: bool


class DriverRef(BaseModel):
    id: uuid.UUID
    name: str


class VehicleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reg_no: str
    has_cold_chain: bool


class StopOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seq: int
    stop_type: StopType
    place: str
    lat: float
    lng: float
    planned_at: datetime | None
    actual_at: datetime | None


class StatusChangeOut(BaseModel):
    from_status: ShipmentStatus | None
    to_status: ShipmentStatus
    at: datetime


class ShipmentOut(BaseModel):
    """What every involved org sees: no costs. `planned_eta` is the plan's estimate at
    approval; `eta` and the route are computed at assignment (null while unassigned)."""

    id: uuid.UUID
    shortage_id: uuid.UUID
    source_request_id: uuid.UUID | None
    purchase_order_id: uuid.UUID | None
    from_org_id: uuid.UUID
    from_org_name: str
    to_org_id: uuid.UUID
    to_org_name: str
    carrier_org_id: uuid.UUID | None
    carrier_org_name: str | None
    product_id: uuid.UUID
    product_code: str
    product_name: str
    qty: int
    requires_cold_chain: bool
    status: ShipmentStatus
    priority: Priority
    required_by: datetime = Field(description="The shortage's deadline.")
    driver: DriverRef | None
    vehicle: VehicleOut | None
    device_id: uuid.UUID | None
    planned_eta: datetime | None
    eta: datetime | None
    route_distance_km: float | None
    route_provider: RouteProvider | None
    pickup: StopOut | None
    drop: StopOut | None
    created_at: datetime
    updated_at: datetime


class ShipmentDetailOut(ShipmentOut):
    route_geometry: dict[str, Any] | None = Field(
        description="GeoJSON LineString ([lng, lat] pairs) of the road route; null until assigned."
    )
    status_history: list[StatusChangeOut]
    last_location: LocationOut | None
    inspection_note_required: bool = Field(
        description="True when the shipment has an open cold-chain excursion: its receipt then "
        "needs an inspection note (business-rules.md §9). Always false until S15."
    )
    receipt: ReceiptOut | None = Field(
        description="What the receiving org recorded (with the reconciliation once every "
        "shipment of the shortage has a receipt). Shown to the receiving org only; null for "
        "the other orgs and before a receipt."
    )
