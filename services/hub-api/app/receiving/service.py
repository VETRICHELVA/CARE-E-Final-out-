"""Receipt and reconciliation (business-rules.md §8, §9; S12).

The receiving org (the shipment's `to` org, a user with `receipt.record`) records what
arrived: expected (the shipment's qty), received, accepted, rejected, condition and an
inspection note. That moves the shipment DELIVERED -> RECONCILED, adds the accepted stock to
the receiver's inventory as a new batch, and, for a purchase order's shipment, moves the
order DISPATCHED -> DELIVERED. Once every shipment of the shortage has a receipt, the
shortage is reconciled: IN_FULFILLMENT -> RECEIVED -> RESOLVED, or PARTIALLY_RESOLVED with a
residual shortage for what was not accepted, which starts matching at once without the
parent's excluded sources.

Lock order, as everywhere: the shortage, then the shipment (then the purchase order). Taking
the shortage first also serializes the receipts of a split's shipments, so exactly one of
them sees "every shipment has a receipt" and reconciles.

Audit (§10): the receipt, the new batch and the shipment's move are the receiver's (its user
and typed reason, in its own org, which is the shortage's). The shortage's move to RECEIVED
follows from the same action and carries the same user and reason (as approval does for
IN_FULFILLMENT). The reconciliation, the shortage's outcome, the residual and the purchase
order's DELIVERED are SYSTEM rows stating only recorded figures; the purchase order's row is
mirrored into the supplier's org without a user."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.auth.models import User
from app.db import flush_or_conflict
from app.domain import reconciliation as rules
from app.domain.events import EventType
from app.domain.fulfillment import ShipmentStatus
from app.domain.shortage import Status
from app.domain.state_machine import InvalidTransition
from app.errors import AppError
from app.events import service as events
from app.inventory.models import InventoryBatch
from app.purchase_orders import service as purchase_orders
from app.purchase_orders.models import PurchaseOrder
from app.receiving.models import Receipt, Reconciliation
from app.receiving.schemas import ReceiptIn
from app.shipments import service as shipments
from app.shipments.models import Shipment
from app.shortages import service as shortages
from app.shortages.models import Shortage, Trigger

RECEIPT = "receipt"
RECONCILIATION = "reconciliation"
BATCH = "inventory_batch"


async def receipt_for(session: AsyncSession, shipment_id: uuid.UUID) -> Receipt | None:
    return await session.scalar(select(Receipt).where(Receipt.shipment_id == shipment_id))


async def reconciliation_for(
    session: AsyncSession, shipment_id: uuid.UUID
) -> Reconciliation | None:
    return await session.scalar(
        select(Reconciliation).where(Reconciliation.shipment_id == shipment_id)
    )


def default_batch_no(shipment: Shipment) -> str:
    return f"RCV-{shipment.id.hex[:8].upper()}"


async def record(
    session: AsyncSession,
    user: User,
    shipment_id: uuid.UUID,
    body: ReceiptIn,
    *,
    now: datetime | None = None,
) -> Receipt:
    """POST /shipments/{id}/receipt. 403 unless the caller's org receives the shipment;
    409 unless it is DELIVERED; 400 if the figures break §9's invariants, if an open
    cold-chain excursion has no inspection note, or if accepted stock has no expiry date;
    409 `conflict` if the batch number is already used for this product at the facility."""
    now = now or datetime.now(UTC)
    shipment = await shipments.get_visible(session, user, shipment_id)
    if shipment.to_org_id != user.org_id:
        raise AppError(403, "forbidden", "Only the receiving organization records a receipt.")
    shortage = await session.get_one(
        Shortage, shipment.shortage_id, with_for_update=True, populate_existing=True
    )
    shipment = await session.get_one(
        Shipment, shipment_id, with_for_update=True, populate_existing=True
    )
    if shipment.status != ShipmentStatus.DELIVERED:
        raise InvalidTransition(shipment.status, ShipmentStatus.RECONCILED)

    figures = {
        "expected": shipment.qty,
        "received": body.received,
        "accepted": body.accepted,
        "rejected": body.rejected,
    }
    if problem := rules.receipt_problem(**figures):
        raise AppError(400, "validation", problem, figures)
    note = (body.inspection_note or "").strip() or None
    if rules.inspection_note_missing(await shipments.has_open_excursion(session, shipment), note):
        raise AppError(
            400,
            "validation",
            "This shipment had a cold-chain excursion: enter an inspection note.",
            {"reason": "inspection_note_required"},
        )
    if body.accepted > 0 and body.expiry_date is None:
        raise AppError(
            400,
            "validation",
            "Enter the expiry date of the accepted stock.",
            {"reason": "expiry_date_required"},
        )

    batch = None
    if body.accepted > 0:
        assert body.expiry_date is not None
        batch = await _add_batch(session, user, shortage, shipment, body)
    receipt = Receipt(
        id=uuid.uuid4(),
        shipment_id=shipment.id,
        shortage_id=shortage.id,
        **figures,
        condition=body.condition,
        inspection_note=note,
        received_by=user.id,
        batch_id=batch.id if batch else None,
    )
    session.add(receipt)
    await session.flush()
    await session.refresh(receipt)  # ts is set by the database
    await audit.record(
        session,
        user,
        RECEIPT,
        receipt.id,
        f"{RECEIPT}.recorded",
        None,
        {
            "shipment_id": shipment.id,
            "shortage_id": shortage.id,
            **figures,
            "condition": receipt.condition,
            "inspection_note": note,
            "batch_id": receipt.batch_id,
        },
        body.reason,
    )
    if batch is not None:
        await audit.record(
            session,
            user,
            BATCH,
            batch.id,
            f"{BATCH}.received",
            None,
            {
                "facility_id": batch.facility_id,
                "product_id": batch.product_id,
                "batch_no": batch.batch_no,
                "on_hand": batch.on_hand,
                "expiry_date": batch.expiry_date,
                "unit_cost_paise": batch.unit_cost_paise,
                "receipt_id": receipt.id,
                "shipment_id": shipment.id,
            },
            body.reason,
        )
        await events.emit(
            session,
            EventType.INVENTORY_CHANGED,
            [user.org_id],
            {"batch_ids": [batch.id], "product_ids": [batch.product_id]},
        )
    if shipment.purchase_order_id is not None:
        await purchase_orders.deliver(session, shortage, shipment.purchase_order_id)
    await shipments.mark_reconciled(
        session, shipment, user, body.reason, now, changes={"receipt_id": receipt.id}
    )
    if await _all_received(session, shortage):
        await shortages.move_shortage(session, shortage, Status.RECEIVED, user, body.reason)
        await _reconcile(session, shortage, now)
    return receipt


async def _add_batch(
    session: AsyncSession, user: User, shortage: Shortage, shipment: Shipment, body: ReceiptIn
) -> InventoryBatch:
    """§9: the accepted stock becomes a new batch at the requesting facility. Its expiry is
    what the receiver entered; its unit cost is the purchase order's price for a purchase
    and 0 for a transfer (the source's cost is never shown to the receiver; CLAUDE.md rule
    6). It is unverified (last_verified_at null) until someone verifies it, as for any new
    batch, so it is not offered to the network before then."""
    unit_cost = 0
    if shipment.purchase_order_id is not None:
        po = await session.get_one(PurchaseOrder, shipment.purchase_order_id)
        unit_cost = po.unit_price_paise
    batch_no = body.batch_no or default_batch_no(shipment)
    batch = InventoryBatch(
        id=uuid.uuid4(),
        org_id=user.org_id,
        facility_id=shortage.facility_id,
        product_id=shipment.product_id,
        batch_no=batch_no,
        on_hand=body.accepted,
        expiry_date=body.expiry_date,
        unit_cost_paise=unit_cost,
    )
    await flush_or_conflict(
        session,
        batch,
        f"Batch {batch_no} of this product already exists at this facility; "
        "enter another batch number.",
    )
    return batch


async def _all_received(session: AsyncSession, shortage: Shortage) -> bool:
    """Every shipment of the shortage has a receipt. Shipments exist only from approval on
    (and a purchase order's from dispatch), so every one belongs to this fulfillment."""
    missing = await session.scalar(
        select(func.count())
        .select_from(Shipment)
        .outerjoin(Receipt, Receipt.shipment_id == Shipment.id)
        .where(Shipment.shortage_id == shortage.id, Receipt.id.is_(None))
    )
    return missing == 0


async def _reconcile(session: AsyncSession, shortage: Shortage, now: datetime) -> None:
    """§9: compare the total accepted with the shortfall; RESOLVED, or PARTIALLY_RESOLVED
    with a residual shortage that starts matching. One Reconciliation per shipment, then
    `reconciliation.completed` to the shortage's org."""
    receipts = list(
        await session.scalars(
            select(Receipt).where(Receipt.shortage_id == shortage.id).order_by(Receipt.ts)
        )
    )
    accepted = sum(r.accepted for r in receipts)
    result = rules.reconcile(shortage.shortfall, accepted)
    residual = None
    if result.residual_qty:
        residual = await _open_residual(session, shortage, result.residual_qty, accepted)
    rows = [
        Reconciliation(
            id=uuid.uuid4(),
            shortage_id=shortage.id,
            shipment_id=r.shipment_id,
            expected=r.expected,
            accepted=r.accepted,
            discrepancy=r.expected - r.accepted,
            outcome=result.outcome,
            residual_shortage_id=residual.id if residual else None,
        )
        for r in receipts
    ]
    session.add_all(rows)
    await session.flush()
    cause = rules.reconciled_reason(shortage.shortfall, accepted, result.residual_qty)
    for row in rows:
        await audit.record(
            session,
            None,
            RECONCILIATION,
            row.id,
            f"{RECONCILIATION}.completed",
            None,
            _snapshot(row),
            cause,
            org_id=shortage.org_id,
        )
    await shortages.move_shortage(session, shortage, result.status, None, cause)
    await events.emit(
        session,
        EventType.RECONCILIATION_COMPLETED,
        [shortage.org_id],
        {
            "shortage_id": shortage.id,
            "outcome": result.outcome,
            "residual_shortage_id": residual.id if residual else None,
        },
    )
    if residual is not None:
        # §9: the residual inherits the parent's exclusions (sources that declined, had a
        # recommendation rejected or rejected a purchase order for it).
        parent_run = await shortages.latest_run(session, shortage.id)
        await shortages.run_match(
            session,
            residual,
            Trigger.CREATE,
            reason=rules.RESIDUAL_CREATED,
            exclude=parent_run.excluded_org_ids if parent_run else (),
            now=now,
        )


def _snapshot(row: Reconciliation) -> dict[str, Any]:
    return {
        "shortage_id": row.shortage_id,
        "shipment_id": row.shipment_id,
        "expected": row.expected,
        "accepted": row.accepted,
        "discrepancy": row.discrepancy,
        "outcome": row.outcome,
        "residual_shortage_id": row.residual_shortage_id,
    }


async def _open_residual(
    session: AsyncSession, parent: Shortage, qty: int, accepted: int
) -> Shortage:
    """§9: qty_required = shortfall − accepted, qty_local_usable = 0, the same product,
    priority and shelf-life minimum, `parent_shortage_id` set. The spec names no other
    field: the facility, deadline, source and requester are the parent's (the need, its
    place and its date have not changed), and the notes are left empty."""
    residual = Shortage(
        id=uuid.uuid4(),
        org_id=parent.org_id,
        facility_id=parent.facility_id,
        product_id=parent.product_id,
        qty_required=qty,
        qty_local_usable=0,
        shortfall=qty,
        required_by=parent.required_by,
        priority=parent.priority,
        min_shelf_life_days=parent.min_shelf_life_days,
        status=Status.OPEN,
        notes=None,
        parent_shortage_id=parent.id,
        created_by=parent.created_by,
        source=parent.source,
    )
    session.add(residual)
    await session.flush()
    await audit.record(
        session,
        None,
        shortages.ENTITY,
        residual.id,
        f"{shortages.ENTITY}.created",
        None,
        {
            **{f: getattr(residual, f) for f in shortages.SNAPSHOT},
            "parent_shortage_id": parent.id,
        },
        rules.residual_reason(parent.shortfall, accepted),
        org_id=parent.org_id,
    )
    return residual
