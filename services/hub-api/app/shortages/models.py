import uuid
from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Entity
from app.domain.shortage import Status


class Priority(StrEnum):
    CRITICAL = "CRITICAL"
    ROUTINE = "ROUTINE"


class ShortageSource(StrEnum):
    FORM = "FORM"
    CHAT = "CHAT"


class Trigger(StrEnum):
    CREATE = "CREATE"
    DECLINE = "DECLINE"
    EXPIRY = "EXPIRY"
    MANUAL = "MANUAL"
    RECOMMENDATION_EXPIRED = "RECOMMENDATION_EXPIRED"
    # §5: a "No eligible source" shortage re-runs when inventory or supplier offers change.
    STOCK_CHANGE = "STOCK_CHANGE"


class SourceType(StrEnum):
    HOSPITAL = "HOSPITAL"
    SUPPLIER = "SUPPLIER"


def _one_of(column: str, values: Iterable[str]) -> str:
    return f"{column} IN ({','.join(repr(str(v)) for v in values)})"


class Shortage(Entity):
    """`shortfall` is computed by the hub (business-rules.md §1); the check keeps it so."""

    __tablename__ = "shortage"
    __table_args__ = (
        CheckConstraint(_one_of("status", Status), name="status"),
        CheckConstraint(_one_of("priority", Priority), name="priority"),
        CheckConstraint(_one_of("source", ShortageSource), name="source"),
        CheckConstraint(
            "qty_required >= 0 AND qty_local_usable >= 0 AND min_shelf_life_days >= 0",
            name="non_negative",
        ),
        CheckConstraint(
            "shortfall = GREATEST(0, qty_required - qty_local_usable)", name="shortfall"
        ),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"), index=True)
    facility_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("facility.id"))
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product.id"))
    qty_required: Mapped[int]
    qty_local_usable: Mapped[int]
    shortfall: Mapped[int]
    required_by: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    priority: Mapped[str] = mapped_column(String(8))
    min_shelf_life_days: Mapped[int]
    status: Mapped[str] = mapped_column(String(24))
    notes: Mapped[str | None]
    parent_shortage_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shortage.id"))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"))
    source: Mapped[str] = mapped_column(String(8))


class MatchRun(Entity):
    """One deterministic match of a shortage. `planned_resolution` is null when nothing is
    eligible; `excluded_org_ids` carry over to the shortage's later runs."""

    __tablename__ = "match_run"
    __table_args__ = (
        UniqueConstraint("shortage_id", "run_no"),
        CheckConstraint(_one_of("triggered_by", Trigger), name="triggered_by"),
    )

    shortage_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shortage.id"))
    run_no: Mapped[int]
    triggered_by: Mapped[str] = mapped_column(String(24))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    excluded_org_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid), default=list)
    planned_resolution: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Candidate(Entity):
    """A source checked in a run. Hospitals carry `transferable_qty` and the batches counted
    (oldest expiry first); suppliers carry `offered_qty`. `gate_results` lists every gate in
    business-rules.md §3 order with its reason; `rank` is set for eligible candidates only."""

    __tablename__ = "candidate"
    __table_args__ = (CheckConstraint(_one_of("source_type", SourceType), name="source_type"),)

    match_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("match_run.id"), index=True)
    source_org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"))
    source_type: Mapped[str] = mapped_column(String(8))
    batch_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid), default=list)
    transferable_qty: Mapped[int | None]
    offered_qty: Mapped[int | None]
    gate_results: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    eligible: Mapped[bool]
    landed_cost_paise: Mapped[int | None] = mapped_column(BigInteger)
    eta_hours: Mapped[float]
    reliability: Mapped[int]
    rank: Mapped[int | None]
