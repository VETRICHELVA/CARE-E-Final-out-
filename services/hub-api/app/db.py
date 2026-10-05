import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated

from fastapi import Depends
from pydantic import AfterValidator, Field
from pydantic_core import PydanticCustomError
from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import settings
from app.errors import AppError


def _nul_free(value: str) -> str:
    if "\x00" in value:
        raise PydanticCustomError("nul_character", "must not contain NUL characters")
    return value


# Input types for values that reach Postgres; without them these were 500s.
# Text cannot hold NUL, and integer (int4) columns stop at 2,147,483,647.
NulFree = AfterValidator(_nul_free)
NulFreeStr = Annotated[str, NulFree]
NonNegInt4 = Annotated[int, Field(ge=0, le=2_147_483_647)]


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class Entity(Base):
    """Every table: UUID `id`, `created_at`, `updated_at` (UTC)."""

    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.clock_timestamp(),
        onupdate=func.clock_timestamp(),
    )


UNIQUE_VIOLATION = "23505"


async def flush_or_conflict(session: AsyncSession, obj: Entity, message: str) -> None:
    """Add/flush `obj` in a savepoint, so a failure leaves the rest of the transaction
    usable (CSV import skips just that row). A unique violation becomes 409 `conflict`;
    any other integrity error (foreign key, check, not null) is a bug and is re-raised."""
    try:
        async with session.begin_nested():
            session.add(obj)
    except IntegrityError as e:
        if getattr(e.orig, "sqlstate", None) != UNIQUE_VIOLATION:
            raise
        raise AppError(409, "conflict", message) from e


engine = create_async_engine(settings.database_url)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
