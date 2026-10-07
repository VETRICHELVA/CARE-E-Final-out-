import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Entity
from app.domain.source_request import HoldStatus, RequestStatus


def _one_of(column: str, values: type[RequestStatus] | type[HoldStatus]) -> str:
    return f"{column} IN ({','.join(repr(str(v)) for v in values)})"


class SourceRequest(Entity):
    """A planned source asked to supply `qty` for a shortage (business-rules.md §7).
    `sla_deadline` is the response deadline; a tentative hold's deadline is on its holds.
    `decline_reason` is what the source typed; `reason_source` says whether it typed one."""

    __tablename__ = "source_request"
    __table_args__ = (
        CheckConstraint(_one_of("status", RequestStatus), name="status"),
        CheckConstraint("qty > 0", name="qty_positive"),
        CheckConstraint(
            "reason_source IS NULL OR reason_source IN ('USER','SYSTEM')", name="reason_source"
        ),
        # A source has at most one open request per shortage.
        Index(
            "uq_source_request_open",
            "shortage_id",
            "source_org_id",
            unique=True,
            postgresql_where=text("status IN ('REQUESTED','TENTATIVE_HOLD')"),
        ),
        # The timer job's scan for overdue requests.
        Index(
            "ix_source_request_open_deadline",
            "sla_deadline",
            postgresql_where=text("status = 'REQUESTED'"),
        ),
    )

    shortage_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shortage.id"), index=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidate.id"))
    source_org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"), index=True)
    qty: Mapped[int]
    status: Mapped[str] = mapped_column(String(16))
    sla_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    responded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decline_reason: Mapped[str | None]
    reason_source: Mapped[str | None] = mapped_column(String(8))


class Hold(Entity):
    """Stock set aside on one batch for one request. TENTATIVE and FIRM holds count as
    `reserved` in every transferable calculation (§2)."""

    __tablename__ = "hold"
    __table_args__ = (
        CheckConstraint(_one_of("status", HoldStatus), name="status"),
        CheckConstraint("qty > 0", name="qty_positive"),
        Index(
            "ix_hold_active_batch",
            "batch_id",
            postgresql_where=text("status IN ('TENTATIVE','FIRM')"),
        ),
        Index(
            "ix_hold_tentative_expiry",
            "expires_at",
            postgresql_where=text("status = 'TENTATIVE'"),
        ),
    )

    source_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_request.id"), index=True
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_batch.id"))
    qty: Mapped[int]
    status: Mapped[str] = mapped_column(String(12))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
