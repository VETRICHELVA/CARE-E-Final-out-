"""Creating shipments (business-rules.md §7 step 5). S09 only creates them, as CREATED;
S11 adds assignment, movement and the shipment endpoints."""

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.auth.models import User
from app.catalog.models import Product
from app.domain.events import EventType
from app.domain.fulfillment import ShipmentStatus
from app.events import service as events
from app.shipments.models import Shipment
from app.shortages.models import Shortage

ENTITY = "shipment"


async def create(
    session: AsyncSession,
    shortage: Shortage,
    *,
    from_org_id: uuid.UUID,
    qty: int,
    planned_eta: datetime | None,
    actor: User | None,
    reason: str | None,
    source_request_id: uuid.UUID | None = None,
    purchase_order_id: uuid.UUID | None = None,
) -> Shipment:
    """One CREATED shipment from `from_org_id` to the shortage's org, for one confirmed
    source request or one dispatched purchase order. Its audit row goes to the shortage's
    org (the trail it follows); `shipment.created` goes to both orgs."""
    product = await session.get_one(Product, shortage.product_id)
    shipment = Shipment(
        id=uuid.uuid4(),
        shortage_id=shortage.id,
        source_request_id=source_request_id,
        purchase_order_id=purchase_order_id,
        from_org_id=from_org_id,
        to_org_id=shortage.org_id,
        product_id=shortage.product_id,
        qty=qty,
        requires_cold_chain=product.requires_cold_chain,
        status=ShipmentStatus.CREATED,
        planned_eta=planned_eta,
    )
    session.add(shipment)
    await session.flush()
    after = {
        "status": shipment.status,
        "shortage_id": shortage.id,
        "source_request_id": source_request_id,
        "purchase_order_id": purchase_order_id,
        "from_org_id": from_org_id,
        "to_org_id": shortage.org_id,
        "qty": qty,
        "requires_cold_chain": shipment.requires_cold_chain,
        "planned_eta": planned_eta,
    }
    await audit.record(
        session, actor, ENTITY, shipment.id, f"{ENTITY}.created", None, after, reason,
        org_id=shortage.org_id,
    )  # fmt: skip
    await events.emit(
        session,
        EventType.SHIPMENT_CREATED,
        [shortage.org_id, from_org_id],
        {"shipment_id": shipment.id, "from": None, "to": shipment.status},
    )
    return shipment
