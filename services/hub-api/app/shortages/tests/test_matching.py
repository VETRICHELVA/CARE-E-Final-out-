"""The matching engine against the database: Scenario 1 (demo-scenarios.md), splits, and the
§2 batch rules, with the clock fixed so expiry-at-delivery days are exact."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.catalog.models import Product, ProductAuthorization, SupplierOffer
from app.conftest import World
from app.inventory.models import InventoryBatch
from app.orgs.models import Facility, Organization, OrgType
from app.recommendations import service as rec_service
from app.recommendations import transitions as recommendations
from app.shortages import service
from app.shortages.models import Candidate, Priority, Shortage, Trigger
from app.shortages.schemas import CandidateOut, MatchRunOut, ShortageCreate
from app.source_requests import holds
from app.source_requests import service as sr_service

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 6, 6, 0, tzinfo=UTC)  # deliveries within the city land the same day
TODAY = NOW.date()
Orgs = dict[str, Organization]


async def facility_of(session: AsyncSession, org: Organization) -> Facility:
    facility = await session.scalar(select(Facility).where(Facility.org_id == org.id))
    assert facility is not None
    return facility


async def add_org(
    session: AsyncSession, name: str, org_type: OrgType, lat: float, lng: float
) -> Organization:
    org = Organization(name=name, type=org_type, lat=lat, lng=lng)
    session.add(org)
    await session.flush()
    session.add(
        Facility(org_id=org.id, name=name, address=name, lat=lat, lng=lng, has_cold_storage=True)
    )
    await session.flush()
    return org


async def add_batch(
    session: AsyncSession,
    org: Organization,
    product: Product,
    *,
    on_hand: int,
    expiry_days: int,
    verified_hours_ago: float = 1,
    batch_no: str = "B-1",
    reserved: int = 0,
    allocated: int = 0,
    safety_stock: int = 0,
) -> InventoryBatch:
    batch = InventoryBatch(
        org_id=org.id,
        facility_id=(await facility_of(session, org)).id,
        product_id=product.id,
        batch_no=batch_no,
        on_hand=on_hand,
        expiry_date=TODAY + timedelta(days=expiry_days),
        unit_cost_paise=1500,
        last_verified_at=NOW - timedelta(hours=verified_hours_ago),
        reserved=reserved,
        allocated=allocated,
        safety_stock=safety_stock,
    )
    session.add(batch)
    await session.flush()
    return batch


def add_offer(
    session: AsyncSession, org: Organization, product: Product, price: int, lead: int, qty: int
) -> None:
    session.add(
        SupplierOffer(
            org_id=org.id,
            product_id=product.id,
            unit_price_paise=price,
            lead_time_hours=lead,
            available_qty=qty,
            updated_at=NOW - timedelta(hours=1),
        )
    )


def authorize(session: AsyncSession, product: Product, *orgs: Organization) -> None:
    session.add_all(ProductAuthorization(org_id=o.id, product_id=product.id) for o in orgs)


@pytest.fixture
async def scenario1(session: AsyncSession, world: World, products: dict[str, Product]) -> Orgs:
    """Scenario 1's seed state for Surgical Kit A, around Hospital A at (12.97, 77.59)."""
    ska = products["SURG-KIT-A"]
    a, b = world.hospital_a, world.hospital_b
    c = await add_org(session, "Hospital C", OrgType.HOSPITAL, 13.02, 77.64)
    d = await add_org(session, "Hospital D", OrgType.HOSPITAL, 12.92, 77.55)
    e = await add_org(session, "Hospital E", OrgType.HOSPITAL, 13.00, 77.52)
    x = await add_org(session, "Supplier X", OrgType.SUPPLIER, 13.10, 77.59)
    y = await add_org(session, "Supplier Y", OrgType.SUPPLIER, 12.85, 77.66)
    z = await add_org(session, "Supplier Z", OrgType.SUPPLIER, 13.05, 77.70)
    await add_batch(session, a, ska, on_hand=150, expiry_days=100)  # the requester's own
    await add_batch(  # 1,000 transferable
        session, b, ska, on_hand=2500, reserved=800, allocated=200, safety_stock=500,
        expiry_days=180, verified_hours_ago=2,
    )  # fmt: skip
    await add_batch(  # 100 transferable
        session, c, ska, on_hand=1400, reserved=800, safety_stock=500,
        expiry_days=200, verified_hours_ago=3,
    )  # fmt: skip
    await add_batch(session, d, ska, on_hand=900, expiry_days=12)
    await add_batch(session, e, ska, on_hand=1200, expiry_days=150)
    add_offer(session, x, ska, price=1400, lead=66, qty=5000)
    add_offer(session, y, ska, price=2800, lead=22, qty=2000)
    add_offer(session, z, products["IV-CAN-20G"], price=500, lead=24, qty=9000)  # not this product
    authorize(session, ska, a, b, c, d, x, y, z, world.supplier)  # everyone except Hospital E
    await session.flush()
    return {o.name: o for o in (a, b, c, d, e, x, y, z)}


async def create(
    session: AsyncSession,
    world: World,
    product: Product,
    priority: Priority = Priority.CRITICAL,
    qty_required: int = 1000,
    qty_local_usable: int = 150,
) -> Shortage:
    body = ShortageCreate(
        facility_id=(await facility_of(session, world.hospital_a)).id,
        product_id=product.id,
        qty_required=qty_required,
        qty_local_usable=qty_local_usable,
        required_by=NOW + timedelta(hours=72),
        priority=priority,
        min_shelf_life_days=None,  # the product default: 30 days for Surgical Kit A
    )
    return await service.create_shortage(session, world.users["a.REQUESTER"], body, now=NOW)


async def latest(session: AsyncSession, shortage: Shortage) -> MatchRunOut:
    run = await service.latest_run(session, shortage.id)
    assert run is not None
    return await service.match_run_out(session, run)


def by_name(out: MatchRunOut) -> dict[str, CandidateOut]:
    return {c.source_org_name: c for c in out.candidates}


def failed(c: CandidateOut) -> list[str | None]:
    return [g.reason for g in c.gate_results if not g.passed]


def plan_of(out: MatchRunOut) -> tuple[str, list[tuple[uuid.UUID, int]], list[uuid.UUID]]:
    p = out.planned_resolution
    assert p is not None
    return (
        p.type,
        [(x.source_org_id, x.qty) for x in p.lines],
        [x.source_org_id for x in p.alternatives],
    )


# --- Scenario 1 ------------------------------------------------------------------------------


async def test_scenario_1_critical(
    session: AsyncSession, world: World, products: dict[str, Product], scenario1: Orgs
) -> None:
    shortage = await create(session, world, products["SURG-KIT-A"])
    assert (shortage.shortfall, shortage.status, shortage.min_shelf_life_days) == (
        850,
        "MATCHING",
        30,
    )
    out = await latest(session, shortage)
    c = by_name(out)
    # Hospital A is the requester; Supplier Z offers another product; Supplier S has no offer.
    assert set(c) == {"Hospital B", "Hospital C", "Hospital D", "Hospital E"} | {
        "Supplier X",
        "Supplier Y",
    }
    assert (c["Hospital B"].eligible, c["Hospital B"].rank, failed(c["Hospital B"])) == (
        True,
        1,
        [],
    )
    assert c["Hospital B"].transferable_qty == 1000
    assert failed(c["Hospital C"]) == ["Only 100 transferable; 850 needed"]
    assert failed(c["Hospital D"]) == ["Expires in 12 days; 30 required"]
    assert failed(c["Hospital E"]) == ["Not authorized to supply this product"]
    assert all(
        not c[h].eligible and c[h].rank is None for h in ("Hospital C", "Hospital D", "Hospital E")
    )
    x, y = c["Supplier X"], c["Supplier Y"]
    assert x.eligible and y.eligible
    assert (y.rank, x.rank) == (2, 3)  # CRITICAL: earliest ETA first
    assert x.eta_hours == pytest.approx(68, abs=1)  # "ETA about 68 h"
    assert y.eta_hours == pytest.approx(24, abs=1)  # "ETA about 24 h"
    assert x.landed_cost_paise == pytest.approx(1_200_000, rel=0.05)  # "about Rs 12,000"
    assert y.landed_cost_paise == pytest.approx(2_400_000, rel=0.05)  # "about Rs 24,000"
    b, sy = scenario1["Hospital B"], scenario1["Supplier Y"]
    assert plan_of(out) == ("TRANSFER", [(b.id, 850)], [sy.id])
    # A hospital's cost would reveal its unit cost: hidden from the requester, kept in storage.
    assert c["Hospital B"].landed_cost_paise is None
    assert out.planned_resolution is not None
    assert [x.landed_cost_paise for x in out.planned_resolution.lines] == [None]
    assert out.planned_resolution.alternatives[0].landed_cost_paise == y.landed_cost_paise
    stored = await session.get_one(Candidate, c["Hospital B"].id)
    assert stored.landed_cost_paise is not None and stored.landed_cost_paise > 0
    assert (out.run_no, out.triggered_by, out.reason) == (1, Trigger.CREATE, None)


async def test_scenario_1_routine_ranks_x_above_y(
    session: AsyncSession, world: World, products: dict[str, Product], scenario1: Orgs
) -> None:
    shortage = await create(session, world, products["SURG-KIT-A"], Priority.ROUTINE)
    out = await latest(session, shortage)
    c = by_name(out)
    assert c["Supplier X"].rank is not None and c["Supplier Y"].rank is not None
    assert c["Supplier X"].rank < c["Supplier Y"].rank  # ROUTINE: lowest landed cost first
    assert plan_of(out)[:2] == ("TRANSFER", [(scenario1["Hospital B"].id, 850)])


async def test_scenario_1_rerun_without_b_buys_from_y_with_x_as_alternative(
    session: AsyncSession, world: World, products: dict[str, Product], scenario1: Orgs
) -> None:
    shortage = await create(session, world, products["SURG-KIT-A"])
    b = scenario1["Hospital B"]
    # B declines its source request (S06), which re-runs matching without B.
    (request,) = await holds.open_requests(session, shortage.id)
    await sr_service.decline(
        session, world.users["b.STORE_MANAGER"], request.id, "Hospital B declined.", now=NOW
    )
    run = await service.latest_run(session, shortage.id)
    assert run is not None
    out = await latest(session, shortage)
    c = by_name(out)
    assert "Hospital B" not in c
    assert failed(c["Hospital C"]) == ["Only 100 transferable; 850 needed"]
    assert failed(c["Hospital D"]) == ["Expires in 12 days; 30 required"]
    assert failed(c["Hospital E"]) == ["Not authorized to supply this product"]
    x, y = scenario1["Supplier X"], scenario1["Supplier Y"]
    assert plan_of(out) == ("BUY", [(y.id, 850)], [x.id])
    assert (run.run_no, run.excluded_org_ids) == (2, [b.id])
    # The exclusion holds for this shortage's later runs. The BUY plan is now awaiting a
    # decision (S09), so the next run comes from rejecting its recommendation, which also
    # leaves the rejected plan's supplier out (§7 step 6).
    rec = await recommendations.open_for(session, shortage.id)
    assert rec is not None
    await rec_service.reject(session, world.users["a.APPROVER"], rec.id, None, now=NOW)
    again = await service.latest_run(session, shortage.id)
    assert again is not None
    assert (again.run_no, again.triggered_by) == (3, "MANUAL")
    assert sorted(again.excluded_org_ids, key=str) == sorted([b.id, y.id], key=str)


async def test_match_runs_are_audited(
    session: AsyncSession, world: World, products: dict[str, Product], scenario1: Orgs
) -> None:
    shortage = await create(session, world, products["SURG-KIT-A"])
    run = await service.latest_run(session, shortage.id)
    assert run is not None
    rows = list(
        await session.scalars(
            select(AuditLog)
            .where(AuditLog.entity_id.in_([shortage.id, run.id]))
            .order_by(AuditLog.ts)
        )
    )
    assert [(r.action, r.actor_id, r.reason_source) for r in rows] == [
        ("shortage.created", world.users["a.REQUESTER"].id, "SYSTEM"),
        ("shortage.status_changed", None, "SYSTEM"),
        ("match_run.created", None, "SYSTEM"),
    ]
    assert rows[0].reason == "No reason was entered."
    assert rows[1].before == {"status": "OPEN"} and rows[1].after == {"status": "MATCHING"}
    assert rows[1].reason == rows[2].reason == "Shortage created."
    assert rows[2].after is not None
    assert (rows[2].after["result"], rows[2].after["eligible"], rows[2].after["rejected"]) == (
        "TRANSFER",
        3,
        3,
    )
    assert {r.org_id for r in rows} == {world.hospital_a.id}


# --- splits, §2 batch rules, no source --------------------------------------------------------


async def split_world(
    session: AsyncSession, world: World, product: Product, stocks: list[int]
) -> list[Organization]:
    """Hospital B holds 1,000; one more hospital per entry in `stocks`; all authorized."""
    await add_batch(session, world.hospital_b, product, on_hand=1000, expiry_days=180)
    others = []
    for n, qty in enumerate(stocks):
        org = await add_org(session, f"Hospital {n}", OrgType.HOSPITAL, 12.95 + n / 100, 77.60)
        await add_batch(session, org, product, on_hand=qty, expiry_days=180)
        others.append(org)
    authorize(session, product, world.hospital_b, *others)
    await session.flush()
    return others


async def test_split_uses_both_hospitals_when_b_is_excluded(
    session: AsyncSession, world: World, products: dict[str, Product]
) -> None:
    ska = products["SURG-KIT-A"]
    p, q = await split_world(session, world, ska, [500, 350])
    shortage = await create(session, world, ska)
    assert plan_of(await latest(session, shortage))[0] == "TRANSFER"  # B alone covers 850
    await service.run_match(
        session,
        shortage,
        Trigger.DECLINE,
        exclude=[world.hospital_b.id],
        reason="Declined.",
        now=NOW,
    )
    out = await latest(session, shortage)
    kind, lines, _ = plan_of(out)
    assert kind == "TRANSFER_SPLIT"
    assert sorted(lines) == sorted([(p.id, 500), (q.id, 350)])
    assert all(c.eligible and failed(c) == [] for c in out.candidates)  # split candidates: qty > 0


async def test_split_never_uses_more_than_three_sources(
    session: AsyncSession, world: World, products: dict[str, Product]
) -> None:
    ska = products["SURG-KIT-A"]
    await split_world(session, world, ska, [300, 300, 300, 300])
    shortage = await create(session, world, ska)
    await service.run_match(
        session,
        shortage,
        Trigger.DECLINE,
        exclude=[world.hospital_b.id],
        reason="Declined.",
        now=NOW,
    )
    out = await latest(session, shortage)
    kind, lines, _ = plan_of(out)
    assert (kind, [qty for _, qty in lines]) == ("TRANSFER_SPLIT", [300, 300, 250])
    assert sorted(c.rank or 0 for c in out.candidates) == [
        1,
        2,
        3,
        4,
    ]  # the 4th is eligible, unused


async def test_only_batches_that_last_until_delivery_count(
    session: AsyncSession, world: World, products: dict[str, Product]
) -> None:
    ska = products["SURG-KIT-A"]
    b = world.hospital_b
    short = await add_batch(session, b, ska, on_hand=900, expiry_days=12, batch_no="SHORT")
    late = await add_batch(session, b, ska, on_hand=600, expiry_days=200, batch_no="LATE")
    soon = await add_batch(session, b, ska, on_hand=400, expiry_days=60, batch_no="SOON")
    authorize(session, ska, b)
    shortage = await create(session, world, ska)
    c = by_name(await latest(session, shortage))["Hospital B"]
    assert (c.eligible, c.transferable_qty) == (True, 1000)  # 600 + 400; the +12-day batch is out
    stored = await session.scalar(select(Candidate.batch_ids).where(Candidate.id == c.id))
    assert stored == [soon.id, late.id]  # oldest expiry first; the +12-day batch is not counted
    assert short.id not in stored


async def test_no_eligible_source_keeps_the_shortage_matching(
    session: AsyncSession, world: World, products: dict[str, Product], scenario1: Orgs
) -> None:
    # Supplier Z offers IV Cannula 20G but is authorized only for Surgical Kit A.
    shortage = await create(session, world, products["IV-CAN-20G"], qty_required=500)
    out = await latest(session, shortage)
    assert (out.planned_resolution, out.reason, shortage.status) == (
        None,
        "No eligible source",
        "MATCHING",
    )
    assert failed(by_name(out)["Supplier Z"]) == ["Not authorized to supply this product"]


async def test_cold_chain_products_need_a_cold_chain_vehicle_on_record(
    session: AsyncSession, world: World, products: dict[str, Product]
) -> None:
    # 2-8 C; this test adds no Vehicle, so no cold-chain vehicle exists (S11 adds the query;
    # app/shipments/tests/test_cold_chain_flow.py covers the passing case).
    rdk = products["DIAG-RDK"]
    await add_batch(session, world.hospital_b, rdk, on_hand=500, expiry_days=240)
    authorize(session, rdk, world.hospital_b)
    shortage = await create(session, world, rdk, qty_required=200, qty_local_usable=0)
    c = by_name(await latest(session, shortage))["Hospital B"]
    assert failed(c) == ["No cold-chain transport available"]


# --- freshness is per batch (business-rules.md §3) ----------------------------------------------


async def test_an_unverified_batch_does_not_hide_a_sources_verified_stock(
    session: AsyncSession, world: World, products: dict[str, Product]
) -> None:
    """A hospital that just received stock has a new, never-verified batch. Its verified
    stock of the same product is still offered; the unverified batch adds nothing."""
    iv = products["IV-CAN-20G"]
    h = await add_org(session, "Hospital H", OrgType.HOSPITAL, 12.95, 77.60)
    await add_batch(session, h, iv, on_hand=2000, expiry_days=200, verified_hours_ago=2)
    received = await add_batch(session, h, iv, on_hand=790, expiry_days=300, batch_no="RCV-1")
    received.last_verified_at = None
    authorize(session, iv, h)
    await session.flush()
    shortage = await create(session, world, iv, qty_required=650)  # 500 short
    out = await latest(session, shortage)
    c = by_name(out)["Hospital H"]
    assert (c.eligible, c.transferable_qty, failed(c)) == (True, 2000, [])
    assert plan_of(out) == ("TRANSFER", [(h.id, 500)], [])


async def test_freshness_fails_only_when_no_counted_batch_is_fresh(
    session: AsyncSession, world: World, products: dict[str, Product]
) -> None:
    iv = products["IV-CAN-20G"]
    h = await add_org(session, "Hospital H", OrgType.HOSPITAL, 12.95, 77.60)
    await add_batch(session, h, iv, on_hand=900, expiry_days=200, verified_hours_ago=30)
    never = await add_batch(session, h, iv, on_hand=900, expiry_days=200, batch_no="B-2")
    never.last_verified_at = None
    authorize(session, iv, h)
    await session.flush()
    shortage = await create(session, world, iv, qty_required=650)  # CRITICAL: 24 h
    c = by_name(await latest(session, shortage))["Hospital H"]
    assert failed(c) == ["Stock last verified 30 h ago"]  # the freshest count is named
