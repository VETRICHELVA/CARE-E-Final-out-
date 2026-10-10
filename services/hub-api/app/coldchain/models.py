import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Entity
from app.domain.coldchain import ColdChainEventType, Severity


def _one_of(column: str, values: type[ColdChainEventType | Severity]) -> str:
    return f"{column} IN ({','.join(repr(str(v)) for v in values)})"


class ColdChainEvent(Entity):
    """A cold-chain event on a shipment (business-rules.md §11), recorded by the hub from
    the readings it received: EXCURSION and RECOVERED from a run of consecutive readings
    (`observed_value` in °C, `threshold` the band's bound), DEVICE_SILENT from the absence
    of readings (`observed_value` and `threshold` in seconds). `ts` is the reading that
    completed the run, or when the silence was noticed. Append-only: an excursion stays on
    record after RECOVERED."""

    __tablename__ = "coldchain_event"
    __table_args__ = (
        CheckConstraint(_one_of("type", ColdChainEventType), name="type"),
        CheckConstraint(_one_of("severity", Severity), name="severity"),
        Index("ix_coldchain_event_shipment_ts", "shipment_id", "ts"),
    )

    shipment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shipment.id"))
    device_id: Mapped[str] = mapped_column(ForeignKey("device.device_id"))
    type: Mapped[str] = mapped_column(String(16))
    threshold: Mapped[float]
    observed_value: Mapped[float]
    severity: Mapped[str] = mapped_column(String(8))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
