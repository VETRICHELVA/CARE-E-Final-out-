import datetime as dt
import uuid

from sqlalchemy import CheckConstraint, Date, Float, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Entity


class ConsumptionRecord(Entity):
    """Units of a product a hospital used on one UTC date: the history forecasts learn from.
    Until hospitals report consumption, every row is seed data from
    scripts/seed/consumption.py, marked `synthetic` (and shown as such in the app)."""

    __tablename__ = "consumption_record"
    __table_args__ = (
        UniqueConstraint("org_id", "product_id", "date"),
        CheckConstraint("qty >= 0", name="qty"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"))
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product.id"), index=True)
    date: Mapped[dt.date] = mapped_column(Date)
    qty: Mapped[int]
    synthetic: Mapped[bool] = mapped_column(default=False)


class Forecast(Entity):
    """One day's forecast consumption of a product at a hospital, with its prediction
    interval. A run replaces the org's rows for that product; `created_at` is the run."""

    __tablename__ = "forecast"
    __table_args__ = (
        UniqueConstraint("org_id", "product_id", "date"),
        CheckConstraint(
            "0 <= lower AND lower <= predicted_qty AND predicted_qty <= upper", name="interval"
        ),  # fmt: skip
        Index("ix_forecast_product", "product_id"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"))
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product.id"))
    date: Mapped[dt.date] = mapped_column(Date)
    predicted_qty: Mapped[float] = mapped_column(Float)
    lower: Mapped[float] = mapped_column(Float)
    upper: Mapped[float] = mapped_column(Float)
    model_version: Mapped[str] = mapped_column(String(32))
