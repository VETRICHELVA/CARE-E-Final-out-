import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.capabilities import Capability
from app.auth.deps import CurrentUser, org_scoped, require
from app.auth.models import User
from app.db import NulFreeStr, SessionDep
from app.errors import AppError
from app.inventory import service
from app.inventory.models import InventoryBatch
from app.inventory.schemas import BatchCreate, BatchOut, BatchUpdate, ImportResult, VerifyIn
from app.orgs.models import OrgType
from app.pagination import Cursor, Limit, Page, paginate
from app.shortages.service import rematch_waiting
from app.source_requests.holds import held_by_batch

router = APIRouter(prefix="/inventory/batches", tags=["inventory"])

# Batch writes: `inventory.edit` in a HOSPITAL org (business-rules.md §2). Each write then
# re-runs other orgs' "No eligible source" shortages for the product (§5), in its transaction.
Editor = Annotated[User, Depends(require(Capability.INVENTORY_EDIT, OrgType.HOSPITAL))]
MAX_CSV_BYTES = 1_000_000


@router.get("")
async def list_batches(
    user: CurrentUser, session: SessionDep, limit: Limit = 50, cursor: Cursor = None
) -> Page[BatchOut]:
    """The caller's own org's batches, each with the hub-computed `transferable`."""
    stmt = org_scoped(select(InventoryBatch), user)
    rows, next_cursor = await paginate(
        session, stmt, InventoryBatch.created_at, InventoryBatch.id, limit, cursor
    )
    today, held = service.today(), await held_by_batch(session, (b.id for b in rows))
    items = [BatchOut.of(b, today, held.get(b.id, 0)) for b in rows]
    return Page[BatchOut](items=items, next_cursor=next_cursor)


async def _out(session: AsyncSession, batch: InventoryBatch) -> BatchOut:
    held = await held_by_batch(session, [batch.id])
    return BatchOut.of(batch, service.today(), held.get(batch.id, 0))


@router.post("", status_code=201)
async def create_batch(body: BatchCreate, user: Editor, session: SessionDep) -> BatchOut:
    batch = await service.create_batch(session, user, body)
    await rematch_waiting(session, [batch.product_id], user.org_id)
    await session.commit()
    return BatchOut.of(batch, service.today())


@router.post(
    "/import",
    openapi_extra={
        "requestBody": {
            "required": True,
            "description": (
                "UTF-8 CSV with a header row. Required columns: product_code, batch_no, "
                "on_hand, expiry_date (YYYY-MM-DD), unit_cost_paise. Optional (blank = 0): "
                "reserved, allocated, safety_stock, quarantined. Max 1 MB."
            ),
            "content": {"text/csv": {"schema": {"type": "string"}}},
        }
    },
)
async def import_batches(
    request: Request,
    facility_id: uuid.UUID,
    user: Editor,
    session: SessionDep,
    reason: NulFreeStr | None = None,
) -> ImportResult:
    """Insert each valid row into `facility_id`; report invalid rows by line number."""
    raw = bytearray()
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > MAX_CSV_BYTES:
            raise AppError(400, "validation", "The CSV file is larger than 1 MB.")
    result, product_ids = await service.import_csv(session, user, facility_id, bytes(raw), reason)
    await rematch_waiting(session, product_ids, user.org_id)
    await session.commit()
    return result


@router.patch("/{batch_id}")
async def update_batch(
    batch_id: uuid.UUID, body: BatchUpdate, user: Editor, session: SessionDep
) -> BatchOut:
    batch = await service.update_batch(session, user, batch_id, body)
    await rematch_waiting(session, [batch.product_id], user.org_id)
    out = await _out(session, batch)
    await session.commit()
    return out


@router.post("/{batch_id}/verify")
async def verify_batch(
    batch_id: uuid.UUID, body: VerifyIn, user: Editor, session: SessionDep
) -> BatchOut:
    """Record a physical count: writes a VerificationEvent, sets last_verified_at, and a
    count that differs from on_hand replaces it."""
    batch = await service.verify_batch(session, user, batch_id, body)
    await rematch_waiting(session, [batch.product_id], user.org_id)
    out = await _out(session, batch)
    await session.commit()
    return out
