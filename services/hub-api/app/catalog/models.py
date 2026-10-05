import uuid

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Entity


class Product(Entity):
    __tablename__ = "product"
    __table_args__ = (
        CheckConstraint(
            "temp_min_c IS NULL OR temp_max_c IS NULL OR temp_min_c <= temp_max_c",
            name="temp_range",
        ),
        CheckConstraint("default_min_shelf_life_days >= 0", name="shelf_life"),
    )

    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str]
    category: Mapped[str]
    unit: Mapped[str] = mapped_column(String(32))
    requires_cold_chain: Mapped[bool] = mapped_column(default=False)
    temp_min_c: Mapped[float | None]
    temp_max_c: Mapped[float | None]
    default_min_shelf_life_days: Mapped[int]


class ProductAuthorization(Entity):
    """Which products an org may supply (the matching authorization gate, S05)."""

    __tablename__ = "product_authorization"
    __table_args__ = (UniqueConstraint("org_id", "product_id"),)

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"))
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product.id"), index=True)


class SupplierOffer(Entity):
    """One offer per supplier org and product. `updated_at` feeds the freshness gate."""

    __tablename__ = "supplier_offer"
    __table_args__ = (
        UniqueConstraint("org_id", "product_id"),
        CheckConstraint(
            "unit_price_paise >= 0 AND lead_time_hours >= 0 AND available_qty >= 0",
            name="non_negative",
        ),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"))
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product.id"), index=True)
    unit_price_paise: Mapped[int]
    lead_time_hours: Mapped[int]
    available_qty: Mapped[int]
