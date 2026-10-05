"""Keyset pagination: `?limit=&cursor=` -> `{items, next_cursor}` (api-and-events.md)."""

import base64
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Query
from pydantic import BaseModel
from sqlalchemy import Select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.errors import AppError

Limit = Annotated[int, Query(ge=1, le=200)]
Cursor = Annotated[str | None, Query()]


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None


async def paginate(
    session: AsyncSession,
    stmt: Select[Any],
    sort: InstrumentedAttribute[datetime],
    id_: InstrumentedAttribute[uuid.UUID],
    limit: int,
    cursor: str | None,
    newest_first: bool = False,
) -> tuple[list[Any], str | None]:
    """Order by (sort, id); the cursor is the last row's (sort, id), base64-encoded."""
    if cursor:
        try:
            ts, last_id = base64.urlsafe_b64decode(cursor).decode().split("|")
            key = (datetime.fromisoformat(ts).astimezone(UTC), uuid.UUID(last_id))
        except (ValueError, OverflowError) as e:  # e.g. year 1 at +14:00 overflows in UTC
            raise AppError(400, "validation", "Invalid cursor.") from e
        pos = tuple_(sort, id_)
        stmt = stmt.where(pos < key if newest_first else pos > key)
    order = (sort.desc(), id_.desc()) if newest_first else (sort, id_)
    rows = list((await session.scalars(stmt.order_by(*order).limit(limit + 1))).all())
    if len(rows) <= limit:
        return rows, None
    last = rows[limit - 1]
    raw = f"{getattr(last, sort.key).isoformat()}|{getattr(last, id_.key)}"
    return rows[:limit], base64.urlsafe_b64encode(raw.encode()).decode()
