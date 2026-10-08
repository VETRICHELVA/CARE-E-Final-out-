"""S09 fixtures, on top of S06's Scenario 1 (relative to the real clock, which the endpoints
read). Purchase order tests import these too."""

import uuid
from datetime import datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.catalog.models import Product
from app.conftest import ClientFor, World
from app.domain.events import EventType
from app.events.models import EventOutbox
from app.orgs.models import OrgType
from app.recommendations import transitions
from app.recommendations.models import Recommendation
from app.shortages.models import MatchRun, Shortage
from app.source_requests.tests import conftest as s06
from app.source_requests.tests.conftest import (
    Orgs,
    add_batch,
    add_org,
    authorize,
    create_shortage,
    requests_of,
)

# S06's fixtures, shared here: the real clock and Scenario 1's seed.
now = s06.now
s1 = s06.s1


@pytest.fixture
async def shortage(
    session: AsyncSession, world: World, products: dict[str, Product], s1: Orgs, now: datetime
) -> Shortage:
    """Scenario 1 step 1-2: 850 short, CRITICAL; TRANSFER from Hospital B, B asked."""
    return await create_shortage(session, world.users["a.REQUESTER"], products["SURG-KIT-A"], now)


@pytest.fixture
async def approver(client_for: ClientFor, world: World) -> httpx.AsyncClient:
    return await client_for(world.users["a.APPROVER"])


@pytest.fixture
async def manager_b(client_for: ClientFor, world: World) -> httpx.AsyncClient:
    return await client_for(world.users["b.STORE_MANAGER"])


async def answer_b(
    session: AsyncSession, manager_b: httpx.AsyncClient, shortage: Shortage, action: str
) -> None:
    """Hospital B accepts or declines its request (Scenario 1 step 3 for decline)."""
    (sr,) = [r for r in await requests_of(session, shortage) if r.status == "REQUESTED"]
    r = await manager_b.post(f"/source-requests/{sr.id}/{action}", json={})
    assert r.status_code == 200, r.text


async def open_rec(session: AsyncSession, shortage: Shortage) -> Recommendation:
    rec = await transitions.open_for(session, shortage.id)
    assert rec is not None
    return rec


async def recs_of(session: AsyncSession, shortage: Shortage) -> list[Recommendation]:
    stmt = select(Recommendation).where(Recommendation.shortage_id == shortage.id)
    return list(await session.scalars(stmt.order_by(Recommendation.created_at)))


async def runs_of(session: AsyncSession, shortage: Shortage) -> list[MatchRun]:
    stmt = select(MatchRun).where(MatchRun.shortage_id == shortage.id)
    return list(await session.scalars(stmt.order_by(MatchRun.run_no)))


async def audit_of(session: AsyncSession, entity_id: uuid.UUID) -> list[AuditLog]:
    stmt = select(AuditLog).where(AuditLog.entity_id == entity_id)
    return list(await session.scalars(stmt.order_by(AuditLog.ts, AuditLog.id)))


async def outbox(session: AsyncSession, event_type: EventType) -> list[dict[str, Any]]:
    stmt = select(EventOutbox).where(EventOutbox.event_type == event_type)
    rows = await session.scalars(stmt.order_by(EventOutbox.created_at))
    return [row.payload for row in rows]


async def add_hospitals(
    session: AsyncSession, product: Product, now: datetime, stock: list[int]
) -> Orgs:
    """Extra hospitals near Hospital A, one batch each (180 days), all authorized."""
    orgs = {}
    for n, qty in enumerate(stock):
        org = await add_org(session, f"Hospital S{n + 1}", OrgType.HOSPITAL, 12.95 + n / 100, 77.60)
        await add_batch(session, org, product, now, on_hand=qty, expiry_days=180)
        orgs[org.name] = org
    authorize(session, product, *orgs.values())
    await session.flush()
    return orgs
