import uuid
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.capabilities import Capability
from app.auth.deps import require
from app.auth.models import User
from app.db import SessionDep
from app.domain.fulfillment import PoStatus
from app.orgs.models import Organization, OrgType
from app.pagination import Cursor, Limit, Page, paginate
from app.purchase_orders import service
from app.purchase_orders.models import PurchaseOrder
from app.purchase_orders.schemas import PurchaseOrderOut
from app.shipments.models import Shipment
from app.shortages.models import Shortage
from app.shortages.schemas import ReasonIn

router = APIRouter(prefix="/purchase-orders", tags=["purchase-orders"])

# api-and-events.md (S09): the supplier side, `po.respond` in a SUPPLIER org.
Supplier = Annotated[User, Depends(require(Capability.PO_RESPOND, OrgType.SUPPLIER))]


async def _out(session: AsyncSession, rows: Sequence[PurchaseOrder]) -> list[PurchaseOrderOut]:
    if not rows:
        return []
    ids = [po.id for po in rows]
    buyers = {
        shortage_id: (org_id, name)
        for shortage_id, org_id, name in await session.execute(
            select(Shortage.id, Organization.id, Organization.name)
            .join(Organization, Organization.id == Shortage.org_id)
            .where(Shortage.id.in_({po.shortage_id for po in rows}))
        )
    }
    stmt = select(Shipment.purchase_order_id, Shipment.id).where(
        Shipment.purchase_order_id.in_(ids)
    )
    shipped = {po_id: shipment_id for po_id, shipment_id in await session.execute(stmt)}
    return [PurchaseOrderOut.of(po, *buyers[po.shortage_id], shipped.get(po.id)) for po in rows]


@router.get("")
async def list_purchase_orders(
    user: Supplier,
    session: SessionDep,
    status: PoStatus | None = None,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> Page[PurchaseOrderOut]:
    """Purchase orders sent to the caller's supplier org, newest first; optional `status`."""
    stmt = select(PurchaseOrder).where(PurchaseOrder.supplier_org_id == user.org_id)
    if status is not None:
        stmt = stmt.where(PurchaseOrder.status == status)
    rows, next_cursor = await paginate(
        session, stmt, PurchaseOrder.created_at, PurchaseOrder.id, limit, cursor, newest_first=True
    )
    return Page[PurchaseOrderOut](items=await _out(session, rows), next_cursor=next_cursor)


@router.post("/{po_id}/acknowledge")
async def acknowledge_purchase_order(
    po_id: uuid.UUID, user: Supplier, session: SessionDep, body: ReasonIn | None = None
) -> PurchaseOrderOut:
    """SENT -> ACKNOWLEDGED. Only the supplier the order went to (403 otherwise); 409 from
    any other status."""
    po = await service.acknowledge(session, user, po_id, body.reason if body else None)
    (out,) = await _out(session, [po])
    await session.commit()
    return out


@router.post("/{po_id}/reject")
async def reject_purchase_order(
    po_id: uuid.UUID, user: Supplier, session: SessionDep, body: ReasonIn | None = None
) -> PurchaseOrderOut:
    """SENT or ACKNOWLEDGED -> REJECTED (reason optional). The shortage goes back to
    MATCHING and matching re-runs without this supplier; 409 from any other status."""
    po = await service.reject(session, user, po_id, body.reason if body else None)
    (out,) = await _out(session, [po])
    await session.commit()
    return out


@router.post("/{po_id}/dispatch")
async def dispatch_purchase_order(
    po_id: uuid.UUID, user: Supplier, session: SessionDep, body: ReasonIn | None = None
) -> PurchaseOrderOut:
    """ACKNOWLEDGED -> DISPATCHED, creating the shipment from the supplier to the hospital
    (`shipment_id`). 409 unless the order has been acknowledged."""
    po = await service.dispatch(session, user, po_id, body.reason if body else None)
    (out,) = await _out(session, [po])
    await session.commit()
    return out
