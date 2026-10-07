import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.fulfillment import PoStatus
from app.purchase_orders.models import PurchaseOrder


class PurchaseOrderOut(BaseModel):
    """A purchase order as the supplier it was sent to sees it."""

    id: uuid.UUID
    shortage_id: uuid.UUID
    supplier_org_id: uuid.UUID
    to_org_id: uuid.UUID = Field(description="The buying hospital (the shortage's org).")
    to_org_name: str
    product_id: uuid.UUID
    qty: int
    unit_price_paise: int
    status: PoStatus
    eta: datetime
    shipment_id: uuid.UUID | None = Field(description="Set once the order is dispatched.")
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(
        cls,
        po: PurchaseOrder,
        to_org_id: uuid.UUID,
        to_org_name: str,
        shipment_id: uuid.UUID | None,
    ) -> "PurchaseOrderOut":
        return cls(
            id=po.id,
            shortage_id=po.shortage_id,
            supplier_org_id=po.supplier_org_id,
            to_org_id=to_org_id,
            to_org_name=to_org_name,
            product_id=po.product_id,
            qty=po.qty,
            unit_price_paise=po.unit_price_paise,
            status=PoStatus(po.status),
            eta=po.eta,
            shipment_id=shipment_id,
            created_at=po.created_at,
            updated_at=po.updated_at,
        )
