"""Purchase orders (business-rules.md §7 steps 5-6, §8): created SENT when a BUY is
approved; the supplier acknowledges, dispatches (which creates the shipment) or rejects
(which sends the shortage back to matching without that supplier).

Lock order, as everywhere: the shortage, then the purchase order. Audit rows go to the
buying org, whose shortage trail they belong to; events go to both orgs."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.auth.models import User
from app.domain.events import EventType
from app.domain.fulfillment import PO_REJECTED, PO_TRANSITIONS, PoStatus
from app.domain.reconciliation import PO_RECEIVED
from app.domain.state_machine import transition
from app.errors import AppError
from app.events import service as events
from app.purchase_orders.models import PurchaseOrder
from app.shipments import service as shipments
from app.shortages.models import Shortage, Trigger
from app.source_requests.service import release_and_rematch

ENTITY = "purchase_order"


async def create(
    session: AsyncSession,
    shortage: Shortage,
    line: dict[str, Any],
    actor: User,
    reason: str | None,
    now: datetime,
) -> PurchaseOrder:
    """A SENT purchase order for an approved BUY line (supplier, qty, unit price, ETA)."""
    po = PurchaseOrder(
        id=uuid.uuid4(),
        shortage_id=shortage.id,
        supplier_org_id=uuid.UUID(line["source_org_id"]),
        product_id=shortage.product_id,
        qty=line["qty"],
        unit_price_paise=line["unit_price_paise"],
        status=PoStatus.SENT,
        eta=now + timedelta(hours=line["eta_hours"]),
    )
    session.add(po)
    await session.flush()
    after = {
        "status": po.status,
        "shortage_id": shortage.id,
        "supplier_org_id": po.supplier_org_id,
        "product_id": po.product_id,
        "qty": po.qty,
        "unit_price_paise": po.unit_price_paise,
        "eta": po.eta,
    }
    await audit.record(
        session, actor, ENTITY, po.id, f"{ENTITY}.created", None, after, reason,
        org_id=shortage.org_id,
    )  # fmt: skip
    await events.emit(
        session,
        EventType.PURCHASE_ORDER_CREATED,
        [shortage.org_id, po.supplier_org_id],
        {"purchase_order_id": po.id, "from": None, "to": po.status},
    )
    return po


async def _move(
    session: AsyncSession,
    shortage: Shortage,
    po: PurchaseOrder,
    to: PoStatus,
    actor: User,
    reason: str | None,
) -> None:
    before = transition(po, to, PO_TRANSITIONS)
    await session.flush()
    await session.refresh(po)
    # The supplier acted: its own org's trail has the row with its user, and the hospital's
    # trail a mirror without the user's id (business-rules.md §10; CLAUDE.md rule 6).
    row = await audit.record(
        session,
        actor,
        ENTITY,
        po.id,
        f"{ENTITY}.status_changed",
        {"status": before},
        {"status": po.status},
        reason,
        org_id=actor.org_id,
    )
    if actor.org_id != shortage.org_id:
        await audit.mirror(session, row, shortage.org_id)
    await events.emit(
        session,
        EventType.PURCHASE_ORDER_STATUS_CHANGED,
        [shortage.org_id, po.supplier_org_id],
        {"purchase_order_id": po.id, "from": before, "to": po.status},
    )


async def _own_locked(
    session: AsyncSession, user: User, po_id: uuid.UUID
) -> tuple[PurchaseOrder, Shortage]:
    """Only the supplier the order went to may answer it (403 for every other org)."""
    po = await session.get(PurchaseOrder, po_id)
    if po is None:
        raise AppError(404, "not_found", "Purchase order not found.")
    if po.supplier_org_id != user.org_id:
        raise AppError(403, "forbidden", "This purchase order was sent to another organization.")
    shortage = await session.get_one(
        Shortage, po.shortage_id, with_for_update=True, populate_existing=True
    )
    po = await session.get_one(PurchaseOrder, po_id, with_for_update=True, populate_existing=True)
    return po, shortage


async def acknowledge(
    session: AsyncSession, user: User, po_id: uuid.UUID, reason: str | None
) -> PurchaseOrder:
    """SENT -> ACKNOWLEDGED; anything else is a 409."""
    po, shortage = await _own_locked(session, user, po_id)
    await _move(session, shortage, po, PoStatus.ACKNOWLEDGED, user, reason)
    return po


async def reject(
    session: AsyncSession,
    user: User,
    po_id: uuid.UUID,
    reason: str | None,
    *,
    now: datetime | None = None,
) -> PurchaseOrder:
    """SENT or ACKNOWLEDGED -> REJECTED, then the shortage goes back to MATCHING and a new
    match run leaves this supplier out for the shortage (§7 step 6, §8)."""
    now = now or datetime.now(UTC)
    po, shortage = await _own_locked(session, user, po_id)
    await _move(session, shortage, po, PoStatus.REJECTED, user, reason)
    await release_and_rematch(
        session,
        shortage,
        PO_REJECTED,
        trigger=Trigger.DECLINE,
        exclude=[po.supplier_org_id],
        now=now,
    )
    return po


async def dispatch(
    session: AsyncSession, user: User, po_id: uuid.UUID, reason: str | None
) -> PurchaseOrder:
    """ACKNOWLEDGED -> DISPATCHED, creating the shipment from the supplier to the hospital.
    A SENT order must be acknowledged first (§8), so dispatching it is a 409."""
    po, shortage = await _own_locked(session, user, po_id)
    await _move(session, shortage, po, PoStatus.DISPATCHED, user, reason)
    await shipments.create(
        session,
        shortage,
        from_org_id=po.supplier_org_id,
        qty=po.qty,
        planned_eta=po.eta,
        actor=user,
        reason=reason,
        purchase_order_id=po.id,
    )
    return po


async def deliver(session: AsyncSession, shortage: Shortage, po_id: uuid.UUID) -> PurchaseOrder:
    """DISPATCHED -> DELIVERED when the buyer records the receipt of the order's shipment
    (S12). It follows from the buyer's receipt, so the row is SYSTEM with that factual
    cause, in the buyer's org (the shortage's trail) and mirrored into the supplier's, which
    must see its order's state (business-rules.md §10). The caller holds the shortage lock."""
    po = await session.get_one(PurchaseOrder, po_id, with_for_update=True, populate_existing=True)
    before = transition(po, PoStatus.DELIVERED, PO_TRANSITIONS)
    await session.flush()
    await session.refresh(po)
    row = await audit.record(
        session,
        None,
        ENTITY,
        po.id,
        f"{ENTITY}.status_changed",
        {"status": before},
        {"status": po.status},
        PO_RECEIVED,
        org_id=shortage.org_id,
    )
    await audit.mirror(session, row, po.supplier_org_id)
    await events.emit(
        session,
        EventType.PURCHASE_ORDER_STATUS_CHANGED,
        [shortage.org_id, po.supplier_org_id],
        {"purchase_order_id": po.id, "from": before, "to": po.status},
    )
    return po
