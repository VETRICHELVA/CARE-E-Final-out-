"""Inventory batches: create, edit, verify and CSV import. Every change writes an audit row
and an `inventory.changed` event (own org only) in the same transaction. Writers are
HOSPITAL-org users with `inventory.edit` (router). The routers then re-run waiting shortages
(`shortages.service.rematch_waiting`, which imports this module)."""

import csv
import io
import uuid
from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.auth.models import User
from app.catalog.models import Product
from app.db import flush_or_conflict
from app.domain.events import EventType
from app.errors import BAD_VALUE, AppError, is_data_error
from app.events import service as events
from app.inventory.models import QTY_FIELDS, InventoryBatch, VerificationEvent
from app.inventory.schemas import (
    BatchCreate,
    BatchUpdate,
    CsvRow,
    ImportResult,
    RowError,
    VerifyIn,
)
from app.orgs.models import Facility

ENTITY = "inventory_batch"
AUDIT_FIELDS = (
    "facility_id",
    "product_id",
    "batch_no",
    *QTY_FIELDS,
    "expiry_date",
    "unit_cost_paise",
)
CSV_REQUIRED = {"product_code", "batch_no", "on_hand", "expiry_date", "unit_cost_paise"}
CSV_OPTIONAL = {"reserved", "allocated", "safety_stock", "quarantined"}


def today() -> date:
    """Expiry is judged on the UTC date (see PROGRESS.md follow-up on IST)."""
    return datetime.now(UTC).date()


def _snapshot(batch: InventoryBatch, fields: Iterable[str] = AUDIT_FIELDS) -> dict[str, Any]:
    return {f: getattr(batch, f) for f in fields}


def _duplicate(batch_no: str) -> str:
    return f"Batch {batch_no} of this product already exists at this facility."


async def _changed(session: AsyncSession, user: User, batches: list[InventoryBatch]) -> None:
    """One `inventory.changed` event per write, to the batches' own org only: other orgs
    never learn about a hospital's stock records, only what matching offers them."""
    if batches:
        await events.emit(
            session,
            EventType.INVENTORY_CHANGED,
            [user.org_id],
            {
                "batch_ids": [b.id for b in batches],
                "product_ids": sorted({b.product_id for b in batches}, key=str),
            },
        )


async def own_facility(session: AsyncSession, user: User, facility_id: uuid.UUID) -> Facility:
    facility = await session.get(Facility, facility_id)
    if facility is None:
        raise AppError(400, "validation", "Unknown facility.", {"facility_id": str(facility_id)})
    if facility.org_id != user.org_id:
        raise AppError(403, "forbidden", "This facility belongs to another organization.")
    return facility


async def own_batch(session: AsyncSession, user: User, batch_id: uuid.UUID) -> InventoryBatch:
    batch = await session.get(InventoryBatch, batch_id)
    if batch is None:
        raise AppError(404, "not_found", "Batch not found.")
    if batch.org_id != user.org_id:
        raise AppError(403, "forbidden", "This batch belongs to another organization.")
    return batch


async def _insert(
    session: AsyncSession, user: User, batch: InventoryBatch, action: str, reason: str | None
) -> None:
    await flush_or_conflict(session, batch, _duplicate(batch.batch_no))
    await audit.record(session, user, ENTITY, batch.id, action, None, _snapshot(batch), reason)


async def create_batch(session: AsyncSession, user: User, body: BatchCreate) -> InventoryBatch:
    await own_facility(session, user, body.facility_id)
    if await session.get(Product, body.product_id) is None:
        raise AppError(400, "validation", "Unknown product.", {"product_id": str(body.product_id)})
    batch = InventoryBatch(org_id=user.org_id, **body.model_dump(exclude={"reason"}))
    await _insert(session, user, batch, f"{ENTITY}.created", body.reason)
    await _changed(session, user, [batch])
    return batch


async def update_batch(
    session: AsyncSession, user: User, batch_id: uuid.UUID, body: BatchUpdate
) -> InventoryBatch:
    batch = await own_batch(session, user, batch_id)
    fields = body.model_dump(exclude={"reason"}, exclude_none=True)
    changes = {k: v for k, v in fields.items() if getattr(batch, k) != v}
    if not changes:
        return batch  # nothing changed, so there is nothing to audit
    before = _snapshot(batch, changes)
    for field, value in changes.items():
        setattr(batch, field, value)
    await flush_or_conflict(session, batch, _duplicate(batch.batch_no))
    await session.refresh(batch)  # updated_at is set by the database
    await audit.record(
        session, user, ENTITY, batch.id, f"{ENTITY}.updated", before, changes, body.reason
    )
    if "expiry_date" in changes:
        await _follow_expiry(session, batch, before.get("expiry_date"))
    await _changed(session, user, [batch])
    return batch


EXPIRY_CORRECTED = "The batch's expiry date was changed."


async def _follow_expiry(session: AsyncSession, batch: InventoryBatch, old: Any) -> None:
    """A live surplus post carries its batch's expiry date (its band, its expiry day and its
    matches use it), so it follows a corrected date. SYSTEM row per post, in the batch's org."""
    from app.domain.surplus import LIVE
    from app.surplus.models import SurplusPost

    posts = await session.scalars(
        select(SurplusPost)
        .where(SurplusPost.batch_id == batch.id, SurplusPost.status.in_(LIVE))
        .with_for_update()
    )
    for post in list(posts):
        post.expiry_date = batch.expiry_date
        await audit.record(
            session,
            None,
            "surplus_post",
            post.id,
            "surplus_post.updated",
            {"expiry_date": old},
            {"expiry_date": batch.expiry_date},
            EXPIRY_CORRECTED,
            org_id=batch.org_id,
        )
    await session.flush()


async def verify_batch(
    session: AsyncSession, user: User, batch_id: uuid.UUID, body: VerifyIn
) -> InventoryBatch:
    """Record a physical count. The count becomes on_hand (business-rules.md §2)."""
    batch = await own_batch(session, user, batch_id)
    before = _snapshot(batch, ("on_hand", "last_verified_at"))
    event = VerificationEvent(
        batch_id=batch.id, user_id=user.id, method=body.method, counted_qty=body.counted_qty
    )
    session.add(event)
    await session.flush()
    batch.on_hand, batch.last_verified_at = body.counted_qty, event.ts
    await session.flush()
    await session.refresh(batch)
    after = {
        **_snapshot(batch, ("on_hand", "last_verified_at")),
        "verification_event_id": event.id,
        "method": event.method,
    }
    await audit.record(
        session, user, ENTITY, batch.id, f"{ENTITY}.verified", before, after, body.reason
    )
    await _changed(session, user, [batch])
    return batch


def _row_error(e: Exception) -> str:
    if isinstance(e, ValidationError):
        return "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
    return e.message if isinstance(e, AppError) else str(e)


def _row_batch(
    row: dict[str | None, Any], products: dict[str, Product], user: User, facility_id: uuid.UUID
) -> InventoryBatch:
    if None in row:
        raise ValueError("This line has more values than the header has columns.")
    values = {k: v.strip() for k, v in row.items() if k and v and v.strip()}
    fields = CsvRow.model_validate(values)
    product = products.get(fields.product_code)
    if product is None:
        raise ValueError(f"Unknown product code {fields.product_code}.")
    return InventoryBatch(
        org_id=user.org_id,
        facility_id=facility_id,
        product_id=product.id,
        **fields.model_dump(exclude={"product_code"}),
    )


async def import_csv(
    session: AsyncSession, user: User, facility_id: uuid.UUID, raw: bytes, reason: str | None
) -> tuple[ImportResult, set[uuid.UUID]]:
    """Insert every valid row; skip invalid ones and report them by CSV line number
    (the header is line 1). A bad file (encoding, header) is a 400 for the whole file.
    Also returns the products of the inserted rows."""
    await own_facility(session, user, facility_id)
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
        reader.fieldnames = [c.strip() for c in reader.fieldnames or []]
        rows = [(reader.line_num, row) for row in reader]
    except (UnicodeDecodeError, csv.Error) as e:
        raise AppError(400, "validation", f"Cannot read the CSV file: {e}") from e
    columns = set(reader.fieldnames)
    if missing := CSV_REQUIRED - columns:
        raise AppError(400, "validation", "Missing columns.", {"missing": sorted(missing)})
    if unknown := columns - CSV_REQUIRED - CSV_OPTIONAL:
        raise AppError(400, "validation", "Unknown columns.", {"unknown": sorted(unknown)})

    # ponytail: the whole catalog (~40 rows), so no raw CSV text reaches SQL; filter by
    # validated codes if the catalog grows to thousands.
    products = {p.code: p for p in await session.scalars(select(Product))}
    # ponytail: one savepoint + audit row per line; fine for store-sized files (1 MB cap)
    inserted: list[InventoryBatch] = []
    errors = []
    for line, row in rows:
        try:
            batch = _row_batch(row, products, user, facility_id)
            await _insert(session, user, batch, f"{ENTITY}.imported", reason)
            inserted.append(batch)
        except (ValueError, AppError) as e:
            errors.append(RowError(line=line, message=_row_error(e)))
        except DBAPIError as e:  # its savepoint was rolled back; the other rows go on
            if not is_data_error(e):
                raise
            errors.append(RowError(line=line, message=BAD_VALUE))
    await _changed(session, user, inserted)
    result = ImportResult(inserted=len(inserted), errors=errors)
    return result, {b.product_id for b in inserted}
