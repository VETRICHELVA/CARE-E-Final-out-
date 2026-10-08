"""Reliability scores and credits (business-rules.md §5, §12; S19): ranking reads the stored
score, reconciliation writes credits for accepted units only and recomputes every source's
score, the nightly recompute, the append-only ledger and GET /orgs/{id}/reliability."""

import uuid
from datetime import datetime

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.catalog.models import Product
from app.conftest import ClientFor, World
from app.domain import reliability as rules
from app.orgs.models import Organization, OrgType
from app.receiving.tests.conftest import receipt
from app.shipments.models import Shipment
from app.shortages.models import Candidate, MatchRun, Priority, Shortage
from app.source_requests.tests.conftest import (
    Orgs,
    add_batch,
    add_org,
    authorize,
    create_shortage,
)
from app.trust import service
from app.trust.models import CreditLedger, ReliabilityScore
from app.worker import WorkerSettings, recompute_reliability

pytestmark = pytest.mark.anyio


async def ledger_of(session: AsyncSession, org_id: uuid.UUID) -> list[CreditLedger]:
    return list(await session.scalars(select(CreditLedger).where(CreditLedger.org_id == org_id)))


async def score_of(session: AsyncSession, org_id: uuid.UUID) -> ReliabilityScore | None:
    return await session.scalar(
        select(ReliabilityScore)
        .where(ReliabilityScore.org_id == org_id)
        .execution_options(populate_existing=True)
    )


# --- ranking reads the stored score ------------------------------------------------------------


async def twins(
    session: AsyncSession, world: World, product: Product, now: datetime, priority: Priority
) -> tuple[list[Organization], Shortage]:
    """Two hospitals equal in every way (place, stock, cost, expiry, verification)."""
    orgs = []
    for n in range(2):
        org = await add_org(session, f"Twin {n}", OrgType.HOSPITAL, 12.95, 77.60)
        await add_batch(session, org, product, now, on_hand=1000, expiry_days=180)
        orgs.append(org)
    authorize(session, product, *orgs)
    await session.flush()
    shortage = await create_shortage(
        session, world.users["a.REQUESTER"], product, now, priority=priority,
        qty_required=850, qty_local_usable=0,
    )  # fmt: skip
    return orgs, shortage


async def ranked(session: AsyncSession, shortage: Shortage) -> list[tuple[uuid.UUID, int]]:
    run = await session.scalar(
        select(MatchRun)
        .where(MatchRun.shortage_id == shortage.id)
        .order_by(MatchRun.run_no.desc())
        .limit(1)
    )
    assert run is not None
    rows = await session.scalars(
        select(Candidate)
        .where(Candidate.match_run_id == run.id, Candidate.eligible.is_(True))
        .order_by(Candidate.rank)
    )
    return [(c.source_org_id, c.reliability) for c in rows]


@pytest.mark.parametrize("priority", [Priority.CRITICAL, Priority.ROUTINE])
async def test_ranking_changes_when_reliability_differs_between_equal_sources(
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
    now: datetime,
    priority: Priority,
) -> None:
    product = products["SURG-KIT-A"]
    orgs, first = await twins(session, world, product, now, priority)
    before = await ranked(session, first)
    assert [score for _, score in before] == [70, 70]  # no history: the default (§5)
    second_placed = before[1][0]

    session.add(ReliabilityScore(org_id=second_placed, score=90, computed_at=now))
    await session.flush()
    another = await create_shortage(
        session, world.users["a.REQUESTER"], product, now, priority=priority,
        qty_required=850, qty_local_usable=0,
    )  # fmt: skip
    after = await ranked(session, another)
    assert after == [(second_placed, 90), (before[0][0], 70)]  # the stored score moved it up
    assert {o.id for o in orgs} == {org_id for org_id, _ in after}


async def test_scores_reads_stored_rows_and_defaults_to_70(
    session: AsyncSession, world: World, now: datetime
) -> None:
    session.add(ReliabilityScore(org_id=world.hospital_b.id, score=42, computed_at=now))
    await session.flush()
    assert await service.scores(session, [world.hospital_a.id, world.hospital_b.id]) == {
        world.hospital_a.id: 70,
        world.hospital_b.id: 42,
    }


# --- reconciliation: credits and the recompute -------------------------------------------------


async def test_reconciling_a_transfer_credits_only_the_accepted_units(
    session: AsyncSession,
    world: World,
    shortage: Shortage,
    delivered: Shipment,
    receiver: httpx.AsyncClient,
) -> None:
    """850 shipped, 805 accepted and 45 rejected: 80 credits (never 85) to Hospital B."""
    body = receipt(850, 805, 45, condition="DAMAGED", inspection_note="Three cartons crushed.")
    r = await receiver.post(f"/shipments/{delivered.id}/receipt", json=body)
    assert r.status_code == 201, r.text

    (entry,) = await ledger_of(session, world.hospital_b.id)
    assert (entry.delta, entry.shortage_id) == (80, shortage.id)
    assert entry.reason == "Transfer reconciled: 805 units accepted by the receiver."
    assert await ledger_of(session, world.hospital_a.id) == []
    (row,) = await session.scalars(select(AuditLog).where(AuditLog.entity_id == entry.id))
    assert (row.org_id, row.actor_id, row.reason_source, row.action) == (
        world.hospital_b.id, None, "SYSTEM", "credit_ledger.credited",
    )  # fmt: skip
    assert row.after == {"delta": 80, "accepted": 805, "shortage_id": str(shortage.id)}


async def test_a_fully_rejected_transfer_earns_no_credits(
    session: AsyncSession, world: World, delivered: Shipment, receiver: httpx.AsyncClient
) -> None:
    body = receipt(850, 0, 850, condition="DAMAGED", inspection_note="Crushed in transit.")
    assert (await receiver.post(f"/shipments/{delivered.id}/receipt", json=body)).status_code == 201
    assert await ledger_of(session, world.hospital_b.id) == []
    # The score still reflects the recorded discrepancy.
    score = await score_of(session, world.hospital_b.id)
    assert score is not None and score.discrepancy_rate == 1.0


async def test_reconciliation_recomputes_the_sources_score_from_its_history(
    session: AsyncSession,
    world: World,
    shortage: Shortage,
    delivered: Shipment,
    receiver: httpx.AsyncClient,
) -> None:
    assert await score_of(session, world.hospital_b.id) is None
    body = receipt(850, 805, 45, condition="DAMAGED", inspection_note="Three cartons crushed.")
    assert (await receiver.post(f"/shipments/{delivered.id}/receipt", json=body)).status_code == 201

    score = await score_of(session, world.hospital_b.id)
    assert score is not None
    assert score.acceptance_rate == 1.0  # B accepted its one request
    assert score.on_time_rate == 1.0  # delivered before required_by
    assert score.discrepancy_rate == pytest.approx(45 / 850)
    assert score.median_response_minutes is not None and score.median_response_minutes >= 0
    assert score.response_speed == pytest.approx(max(0, 1 - score.median_response_minutes / 15))
    components = rules.Components(
        score.acceptance_rate,
        score.median_response_minutes,
        score.response_speed,
        score.on_time_rate,
        score.discrepancy_rate,
    )
    assert score.score == rules.score(components)
    assert 90 <= score.score <= 100  # 40 + 25 + 20 × (1 − 0.053) + ~15


async def test_a_purchase_earns_no_credits_and_the_supplier_keeps_the_default_score(
    session: AsyncSession,
    s1: Orgs,
    po_delivered: Shipment,
    receiver: httpx.AsyncClient,
) -> None:
    """A supplier answers no source requests, so its acceptance component has no history and
    its score stays 70; its delivery and discrepancy are still recorded."""
    y = s1["Supplier Y"]
    body = receipt(790, 790, expiry_date="2027-03-31")
    assert (
        await receiver.post(f"/shipments/{po_delivered.id}/receipt", json=body)
    ).status_code == 201
    assert await ledger_of(session, y.id) == []
    score = await score_of(session, y.id)
    assert score is not None
    assert (score.acceptance_rate, score.on_time_rate) == (None, 1.0)
    assert score.discrepancy_rate == pytest.approx(60 / 850)  # Scenario 1: 790 of 850 arrived
    assert score.score == 70


async def test_the_credit_ledger_is_append_only(
    session: AsyncSession, world: World, delivered: Shipment, receiver: httpx.AsyncClient
) -> None:
    body = receipt(850, 850)
    assert (await receiver.post(f"/shipments/{delivered.id}/receipt", json=body)).status_code == 201
    (entry,) = await ledger_of(session, world.hospital_b.id)
    assert entry.delta == 85
    for sql in (
        "UPDATE credit_ledger SET delta = 1000 WHERE id = :id",
        "DELETE FROM credit_ledger WHERE id = :id",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            async with session.begin_nested():
                await session.execute(text(sql), {"id": entry.id})


# --- the nightly recompute ---------------------------------------------------------------------


async def test_the_nightly_job_recomputes_every_hospital_and_supplier(
    session: AsyncSession, world: World, now: datetime
) -> None:
    done = await service.recompute_all(session, now)
    assert done >= 3
    for org in (world.hospital_a, world.hospital_b, world.supplier):
        score = await score_of(session, org.id)
        assert score is not None and score.score == 70 and score.computed_at == now
    assert await score_of(session, world.platform.id) is None  # not a source
    assert await service.recompute_all(session, now) == done  # idempotent


def test_the_nightly_job_is_scheduled_once_a_night() -> None:
    (job,) = [j for j in WorkerSettings.cron_jobs if j.coroutine is recompute_reliability]
    assert (job.hour, job.minute, job.second, job.unique) == (2, 0, 0, True)


# --- GET /orgs/{id}/reliability ----------------------------------------------------------------


async def test_get_reliability(
    session: AsyncSession,
    world: World,
    delivered: Shipment,
    receiver: httpx.AsyncClient,
    client_for: ClientFor,
) -> None:
    body = receipt(850, 805, 45, condition="DAMAGED", inspection_note="Three cartons crushed.")
    assert (await receiver.post(f"/shipments/{delivered.id}/receipt", json=body)).status_code == 201
    stored = await score_of(session, world.hospital_b.id)
    assert stored is not None

    own = await (await client_for(world.users["b.STORE_MANAGER"])).get(
        f"/orgs/{world.hospital_b.id}/reliability"
    )
    assert own.status_code == 200, own.text
    data = own.json()
    assert (data["score"], data["has_history"], data["credits"]) == (stored.score, True, 80)
    assert data["discrepancy_rate"] == pytest.approx(45 / 850)

    # Another org reads the score and its components, never the credit balance.
    other = await (await client_for(world.users["a.REQUESTER"])).get(
        f"/orgs/{world.hospital_b.id}/reliability"
    )
    assert other.status_code == 200
    assert (other.json()["score"], other.json()["credits"]) == (stored.score, None)


async def test_get_reliability_without_history_unknown_org_and_anonymous(
    world: World, client_for: ClientFor
) -> None:
    client = await client_for(world.users["s.SUPPLIER_DESK"])
    r = await client.get(f"/orgs/{world.supplier.id}/reliability")
    assert r.status_code == 200
    assert r.json() == {
        "org_id": str(world.supplier.id),
        "score": 70,
        "has_history": False,
        "acceptance_rate": None,
        "response_speed": None,
        "median_response_minutes": None,
        "on_time_rate": None,
        "discrepancy_rate": None,
        "computed_at": None,
        "credits": 0,
    }
    assert (await client.get(f"/orgs/{uuid.uuid4()}/reliability")).status_code == 404
    anonymous = await client_for()
    assert (await anonymous.get(f"/orgs/{world.supplier.id}/reliability")).status_code == 401
