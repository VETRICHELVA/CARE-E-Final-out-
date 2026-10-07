import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Entity
from app.domain.fulfillment import PoStatus


class PurchaseOrder(Entity):
    """An approved BUY (business-rules.md §7 step 5): sent to the supplier, which
    acknowledges, rejects or dispatches it. The buyer is the shortage's org."""

    __tablename__ = "purchase_order"
    __table_args__ = (
        CheckConstraint(f"status IN ({','.join(repr(str(v)) for v in PoStatus)})", name="status"),
        CheckConstraint("qty > 0 AND unit_price_paise >= 0", name="amounts"),
    )

    shortage_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shortage.id"), index=True)
    supplier_org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product.id"))
    qty: Mapped[int]
    unit_price_paise: Mapped[int]
    status: Mapped[str] = mapped_column(String(16))
    eta: Mapped[datetime] = mapped_column(DateTime(timezone=True))
