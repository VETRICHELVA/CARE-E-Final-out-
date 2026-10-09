import datetime as dt
import uuid

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Entity
from app.domain.surplus import LIVE, MatchKind, SurplusStatus


def _one_of(column: str, values: type[SurplusStatus | MatchKind]) -> str:
    return f"{column} IN ({','.join(repr(str(v)) for v in values)})"


class SurplusPost(Entity):
    """A hospital's offer of one batch's excess to the network (S18). `qty` is what it
    posted; what it offers is never more than the batch's current transferable (CLAUDE.md
    rule 4), so readers compute `offered_qty` from the batch each time. `expiry_date` is the
    batch's when posted; the post expires on that date. One live post per batch."""

    __tablename__ = "surplus_post"
    __table_args__ = (
        CheckConstraint(_one_of("status", SurplusStatus), name="status"),
        CheckConstraint("qty > 0", name="qty"),
        CheckConstraint("min_price_paise >= 0", name="min_price"),
        Index(
            "uq_surplus_post_live_batch",
            "batch_id",
            unique=True,
            postgresql_where=text(f"status IN ({','.join(repr(str(s)) for s in LIVE)})"),
        ),
        Index("ix_surplus_post_product_status", "product_id", "status"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"), index=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_batch.id"))
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product.id"))
    qty: Mapped[int]
    expiry_date: Mapped[dt.date] = mapped_column(Date)
    min_price_paise: Mapped[int | None]
    status: Mapped[str] = mapped_column(String(12))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"))


class SurplusMatch(Entity):
    """One org a surplus post was matched to: its open shortage of the product, or its
    forecast stock-out within 14 days. That org sees the post (qty, expiry band, location)."""

    __tablename__ = "surplus_match"
    __table_args__ = (
        UniqueConstraint("surplus_id", "org_id"),
        CheckConstraint(_one_of("kind", MatchKind), name="kind"),
        CheckConstraint(
            "(kind = 'SHORTAGE') = (shortage_id IS NOT NULL) "
            "AND (kind = 'FORECAST') = (stockout_date IS NOT NULL)",
            name="reason",
        ),
        Index("ix_surplus_match_org", "org_id"),
    )

    surplus_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("surplus_post.id"))
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"))
    kind: Mapped[str] = mapped_column(String(10))
    shortage_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shortage.id"))
    stockout_date: Mapped[dt.date | None] = mapped_column(Date)
