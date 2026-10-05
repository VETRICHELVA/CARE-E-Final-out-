import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Entity
from app.orgs.models import Organization


class Role(Entity):
    __tablename__ = "role"

    name: Mapped[str] = mapped_column(String(32), unique=True)


class UserRole(Entity):
    __tablename__ = "user_role"
    __table_args__ = (UniqueConstraint("user_id", "role_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("role.id"))


class User(Entity):
    __tablename__ = "app_user"  # "user" is reserved in Postgres

    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str]
    full_name: Mapped[str]
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"), index=True)
    is_active: Mapped[bool] = mapped_column(default=True)

    org: Mapped[Organization] = relationship(lazy="joined")
    roles: Mapped[list[Role]] = relationship(secondary="user_role", lazy="selectin")


class RefreshToken(Entity):
    """One row per issued refresh token. A login starts a family; each refresh rotates
    within it. Access tokens carry the family id, so revoking the family logs out both."""

    __tablename__ = "refresh_token"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    family_id: Mapped[uuid.UUID] = mapped_column(index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
