"""Network demand for suppliers (api-and-events.md, S10): open shortfall totals per product,
with nothing that identifies a hospital."""

import uuid
from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.shortage import Status
from app.shortages.models import Shortage

# Shortages still looking for stock: past a chat draft (DRAFT), not yet sourced
# (IN_FULFILLMENT and later), not CANCELLED.
OPEN_DEMAND = (Status.OPEN, Status.MATCHING, Status.AWAITING_DECISION)


async def open_shortfall(
    session: AsyncSession, product_ids: Iterable[uuid.UUID], caller_org_id: uuid.UUID
) -> dict[uuid.UUID, int]:
    """Total shortfall of other orgs' open shortages, per product. Only the sum leaves the
    database: no row, org or count."""
    ids = set(product_ids)
    if not ids:
        return {}
    stmt = (
        select(Shortage.product_id, func.sum(Shortage.shortfall))
        .where(
            Shortage.product_id.in_(ids),
            Shortage.status.in_(OPEN_DEMAND),
            Shortage.org_id != caller_org_id,
        )
        .group_by(Shortage.product_id)
    )
    return {product_id: int(total) for product_id, total in await session.execute(stmt)}
