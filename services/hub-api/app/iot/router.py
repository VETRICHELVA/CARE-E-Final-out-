from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.auth.capabilities import Capability, ServiceScope
from app.auth.deps import org_scoped, require, require_scope
from app.auth.models import User
from app.db import SessionDep
from app.iot import service
from app.iot.models import Device
from app.iot.schemas import DeviceOut, TelemetryBatch, TelemetryResult
from app.pagination import Cursor, Limit, Page, paginate

router = APIRouter(tags=["iot"])

Dispatcher = Annotated[User, Depends(require(Capability.SHIPMENT_ASSIGN))]
IngestService = Annotated[ServiceScope, Depends(require_scope(ServiceScope.TELEMETRY_WRITE))]


@router.post("/internal/telemetry")
async def post_telemetry(
    body: TelemetryBatch, _: IngestService, session: SessionDep
) -> TelemetryResult:
    """For services/iot-ingest only (ingest token, scope `telemetry.write`). Idempotent:
    a reading whose (device_id, ts) is already stored counts as a duplicate."""
    result = await service.ingest(session, body)
    await session.commit()
    return result


@router.get("/devices")
async def list_devices(
    user: Dispatcher, session: SessionDep, limit: Limit = 50, cursor: Cursor = None
) -> Page[DeviceOut]:
    """The caller's own org's devices, with last_seen and battery."""
    stmt = org_scoped(select(Device), user)
    rows, next_cursor = await paginate(session, stmt, Device.created_at, Device.id, limit, cursor)
    return Page[DeviceOut](
        items=[DeviceOut.model_validate(d) for d in rows], next_cursor=next_cursor
    )
