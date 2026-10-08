"""Races on a database whose transactions really commit: two approvers deciding at once, and
the arq timer job after a restart. Each test makes its own product and orgs."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.models import AuditLog
from app.auth import service as auth_service
from app.auth.models import User
from app.catalog.models import Product
from app.db import get_session
from app.main import create_app
from app.orgs.models import OrgType
from app.purchase_orders.models import PurchaseOrder
from app.recommendations.models import Recommendation
from app.source_requests.tests import conftest as s06
from app.source_requests.tests.conftest import (
    add_offer,
    add_org,
    add_user,
    authorize,
    create_shortage,
    unique,
)
from app.worker import expire_recommendations, startup

pytestmark = pytest.mark.anyio
Maker = async_sessionmaker[AsyncSession]

committed = s06.committed
committed_db_url = s06.committed_db_url


async def setup(sm: Maker, now: datetime) -> tuple[Recommendation, list[User]]:
    """A hospital 850 short of a product only one supplier offers: a BUY recommendation."""
    tag = unique()
    async with sm() as session:
        product = Product(
            code=f"R-{tag}",
            name=f"Race kit {tag}",
            category="Test",
            unit="each",
            default_min_shelf_life_days=30,
        )
        session.add(product)
        supplier = await add_org(session, f"Supplier {tag}", OrgType.SUPPLIER, 12.85, 77.66)
        add_offer(session, supplier, product, now, price=2800, lead=22, qty=2000)
        authorize(session, product, supplier)
        hospital = await add_org(session, f"Hospital {tag}", OrgType.HOSPITAL, 12.97, 77.59)
        requester = await add_user(session, hospital, "REQUESTER", f"req-{tag}@a.test")
        approvers = [
            await add_user(session, hospital, "APPROVER", f"ap{n}-{tag}@a.test") for n in (1, 2)
        ]
        shortage = await create_shortage(
            session, requester, product, now, qty_required=850, qty_local_usable=0
        )
        rec = await session.scalar(
            select(Recommendation).where(Recommendation.shortage_id == shortage.id)
        )
        assert rec is not None and rec.type == "BUY"
        await session.commit()
    return rec, approvers


@pytest.fixture
async def client(committed: Maker) -> AsyncIterator[httpx.AsyncClient]:
    """The real app, each request on its own committed session (as in production)."""
    app = create_app()

    async def session_per_request() -> AsyncIterator[AsyncSession]:
        async with committed() as s:
            yield s

    app.dependency_overrides[get_session] = session_per_request
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test/api/v1"
    ) as c:
        yield c


async def token(sm: Maker, user: User) -> dict[str, str]:
    async with sm() as session:
        pair = await auth_service.issue_tokens(session, await session.get_one(User, user.id))
        await session.commit()
    return {"Authorization": f"Bearer {pair.access_token}"}


async def test_two_approvers_at_once_exactly_one_decides(
    committed: Maker, client: httpx.AsyncClient
) -> None:
    rec, approvers = await setup(committed, datetime.now(UTC))
    first, second = [await token(committed, u) for u in approvers]
    approve, reject = await asyncio.gather(
        client.post(f"/recommendations/{rec.id}/approve", headers=first),
        client.post(f"/recommendations/{rec.id}/reject", headers=second),
    )
    codes = sorted([approve.status_code, reject.status_code])
    assert codes == [200, 409]
    loser = approve if approve.status_code == 409 else reject
    assert loser.json()["code"] == "invalid_transition"
    async with committed() as session:
        decided = await session.get_one(Recommendation, rec.id)
        assert decided.status in ("APPROVED", "REJECTED")
        orders = await session.scalar(
            select(func.count())
            .select_from(PurchaseOrder)
            .where(PurchaseOrder.shortage_id == rec.shortage_id)
        )
        assert orders == (1 if decided.status == "APPROVED" else 0)
        decisions = await session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.entity_id == rec.id,
                AuditLog.action == "recommendation.status_changed",
            )
        )
        assert decisions == 1


async def test_a_restarted_worker_expires_overdue_recommendations(committed: Maker) -> None:
    """The recommendation lapsed while no worker ran; the job's first (startup) run expires
    it, and a second run finds nothing more for it."""
    rec, _ = await setup(committed, datetime.now(UTC) - timedelta(minutes=31))
    ctx: dict[str, object] = {"sessionmaker": committed}
    await startup(ctx)
    assert await expire_recommendations(ctx) >= 1
    await expire_recommendations(ctx)
    async with committed() as session:
        expired = await session.get_one(Recommendation, rec.id)
        assert (expired.status, expired.reason) == ("EXPIRED", "Recommendation validity passed.")
        rows = await session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.entity_id == rec.id, AuditLog.after["status"].astext == "EXPIRED")
        )
        assert rows == 1
        # The re-run planned the same BUY again (no source declined), so a new one waits.
        fresh = await session.scalar(
            select(func.count())
            .select_from(Recommendation)
            .where(
                Recommendation.shortage_id == rec.shortage_id, Recommendation.status == "PENDING"
            )
        )
        assert fresh == 1
