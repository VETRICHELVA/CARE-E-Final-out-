import uuid
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Entity


class OrgType(StrEnum):
    HOSPITAL = "HOSPITAL"
    SUPPLIER = "SUPPLIER"
    LOGISTICS = "LOGISTICS"
    PLATFORM = "PLATFORM"


class OrgStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class Organization(Entity):
    __tablename__ = "organization"
    __table_args__ = (
        CheckConstraint("type IN ('HOSPITAL','SUPPLIER','LOGISTICS','PLATFORM')", name="type"),
        CheckConstraint("status IN ('ACTIVE','SUSPENDED')", name="status"),
    )

    name: Mapped[str]
    type: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default=OrgStatus.ACTIVE)
    lat: Mapped[float]
    lng: Mapped[float]


class Facility(Entity):
    __tablename__ = "facility"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"), index=True)
    name: Mapped[str]
    address: Mapped[str]
    lat: Mapped[float]
    lng: Mapped[float]
    has_cold_storage: Mapped[bool] = mapped_column(default=False)
