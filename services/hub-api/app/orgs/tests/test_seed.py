"""The demo seed (S20, demo-scenarios.md): what it creates, that a second run changes nothing,
and that matching on the seeded data gives each scenario's figures, at any time of day."""

import math
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import seed
from app.auth.models import User
from app.catalog.models import Product, ProductAuthorization, SupplierOffer
from app.conftest import ClientFor
from app.inventory.models import InventoryBatch
from app.iot.models import Device
from app.orgs.models import Facility, Organization, OrgStatus, OrgType
from app.shipments.models import Driver, Vehicle
from app.shortages import service as shortages
from app.shortages.models import Priority, Shortage
from app.shortages.schemas import CandidateOut, ShortageCreate

pytestmark = pytest.mark.anyio

MORNING = datetime(2026, 10, 6, 6, 0, tzinfo=UTC)
# 23:30 UTC: a delivery from Hospital D lands after midnight UTC, the S05 -> S20 case.
LATE = datetime(2026, 10, 6, 23, 30, tzinfo=UTC)


async def snapshot(session: AsyncSession) -> dict[str, Any]:
    """Everything the seed writes, without ids or timestamps the database sets."""
    names = dict((o.id, o.name) for o in await session.scalars(select(Organization)))
    codes = dict((p.id, p.code) for p in await session.scalars(select(Product)))
    rows: dict[str, Any] = {
        "orgs": sorted((o.name, o.type, o.status, o.lat, o.lng) for o in
                       await session.scalars(select(Organization))),
        "facilities": sorted((names[f.org_id], f.name, f.has_cold_storage, f.lat, f.lng) for f in
                             await session.scalars(select(Facility))),
        "users": sorted((u.email, names[u.org_id], u.full_name, tuple(r.name for r in u.roles))
                        for u in await session.scalars(select(User))),
        "authorizations": sorted((names[a.org_id], codes[a.product_id]) for a in
                                 await session.scalars(select(ProductAuthorization))),
        "batches": sorted(
            (names[b.org_id], codes[b.product_id], b.batch_no, b.on_hand, b.reserved,
             b.allocated, b.safety_stock, b.quarantined, b.expiry_date, b.unit_cost_paise,
             b.last_verified_at)
            for b in await session.scalars(select(InventoryBatch))
        ),
        "offers": sorted((names[o.org_id], codes[o.product_id], o.unit_price_paise,
                          o.lead_time_hours, o.available_qty, o.updated_at)
                         for o in await session.scalars(select(SupplierOffer))),
        "vehicles": sorted((v.reg_no, v.has_cold_chain) for v in
                           await session.scalars(select(Vehicle))),
        "drivers": sorted((d.phone, d.active) for d in await session.scalars(select(Driver))),
        "devices": sorted((names[d.org_id], d.device_id) for d in
                          await session.scalars(select(Device))),
    }  # fmt: skip
    return rows


async def test_the_seed_creates_every_org_and_a_second_run_changes_nothing(
    session: AsyncSession,
) -> None:
    assert await seed.seed(session, MORNING) == [o[0] for o in seed.ORGS]
    first = await snapshot(session)
    assert await seed.seed(session, MORNING) == []
    assert await snapshot(session) == first

    orgs = {name: org_type for name, org_type, *_ in first["orgs"]}
    assert sorted(n for n, t in orgs.items() if t == OrgType.HOSPITAL) == [
        f"Hospital {x}" for x in "ABCDEF"
    ]
    assert sorted(n for n, t in orgs.items() if t == OrgType.SUPPLIER) == [
        "Supplier X",
        "Supplier Y",
        "Supplier Z",
    ]
    assert orgs["SwiftMed Logistics"] == OrgType.LOGISTICS
    assert orgs["CARE-E Platform"] == OrgType.PLATFORM
    # One user per role per org, plus Priya (SwiftMed's second driver).
    assert len(first["users"]) == 1 + 6 * 5 + 3 * 2 + 3 + 1
    assert "approver@hospital-f.demo" in {u[0] for u in first["users"]}
    assert await session.scalar(select(func.count()).select_from(Product)) == 40
    # Every hospital and supplier for every product, except Hospital E for Surgical Kit A.
    assert len(first["authorizations"]) == 9 * 40 - 1
    assert ("Hospital E", "SURG-KIT-A") not in first["authorizations"]
    # One cold box; two drivers; two vans, one cold-chain.
    assert first["devices"] == [("SwiftMed Logistics", "cb-01")]
    assert len(first["drivers"]) == 2
    assert first["vehicles"] == [("KA-01-SM-0001", False), ("KA-01-SM-0002", True)]
    # C and F have cold storage, one facility per hospital.
    assert [(f[0], f[2]) for f in first["facilities"]] == [
        (f"Hospital {x}", x in "CF") for x in "ABCDEF"
    ]
    # Supplier Z has no Surgical Kit A offer; nobody offers the e2e tests' own products.
    offered = {(o[0], o[1]) for o in first["offers"]}
    assert ("Supplier Z", "SURG-KIT-A") not in offered
    assert not {code for _, code in offered} & seed.E2E_PRODUCTS
    stocked = {b[1] for b in first["batches"]}
    assert not stocked & seed.E2E_PRODUCTS


async def test_a_later_run_creates_only_what_is_missing(session: AsyncSession) -> None:
    """Re-seeding never changes existing stock, offers, orgs or authorizations: that would be a
    state change with no audit row (CLAUDE.md rule 5). `make demo-reset` resets instead."""
    await seed.seed(session, MORNING)
    names = {o.name: o for o in await session.scalars(select(Organization))}
    kit = await session.scalar(select(Product).where(Product.code == "SURG-KIT-A"))
    assert kit is not None
    b_batch = await session.scalar(
        select(InventoryBatch).where(
            InventoryBatch.org_id == names["Hospital B"].id, InventoryBatch.product_id == kit.id
        )
    )
    assert b_batch is not None
    # What the demo did since: B counted its stock, Y repriced, C was suspended, E authorized,
    # D's batch was removed from the seed's view (here: deleted).
    counted = MORNING + timedelta(hours=3)
    b_batch.on_hand, b_batch.reserved, b_batch.last_verified_at = 1_234, 17, counted
    y_offer = await session.scalar(
        select(SupplierOffer).where(
            SupplierOffer.org_id == names["Supplier Y"].id, SupplierOffer.product_id == kit.id
        )
    )
    assert y_offer is not None
    y_offer.unit_price_paise, y_offer.available_qty = 3_100, 7
    names["Hospital C"].status = OrgStatus.SUSPENDED
    session.add(ProductAuthorization(org_id=names["Hospital E"].id, product_id=kit.id))
    d_batch = await session.scalar(
        select(InventoryBatch).where(
            InventoryBatch.org_id == names["Hospital D"].id, InventoryBatch.product_id == kit.id
        )
    )
    await session.delete(d_batch)
    await session.flush()
    before = await snapshot(session)

    later = MORNING + timedelta(days=1)
    assert await seed.seed(session, later) == []
    after = await snapshot(session)
    b_kit = next(b for b in after["batches"] if b[:2] == ("Hospital B", "SURG-KIT-A"))
    assert (b_kit[3], b_kit[4], b_kit[10]) == (1_234, 17, counted)
    y_kit = next(o for o in after["offers"] if o[:2] == ("Supplier Y", "SURG-KIT-A"))
    assert (y_kit[2], y_kit[4]) == (3_100, 7)
    assert ("Hospital C", OrgType.HOSPITAL, OrgStatus.SUSPENDED) in {o[:3] for o in after["orgs"]}
    assert ("Hospital E", "SURG-KIT-A") in after["authorizations"]
    # Only the missing batch came back, with the spec's numbers as of the later run.
    added = [b for b in after["batches"] if b not in before["batches"]]
    assert [(b[0], b[1], b[3], b[10]) for b in added] == [
        ("Hospital D", "SURG-KIT-A", 900, later - timedelta(hours=1))
    ]
    assert {k: v for k, v in after.items() if k != "batches"} == {
        k: v for k, v in before.items() if k != "batches"
    }
    assert [b for b in after["batches"] if b not in added] == before["batches"]


async def test_an_older_database_keeps_its_own_rows(session: AsyncSession) -> None:
    """An S02-era DB: Hospital A elsewhere, with a cold store. The seed adds the other orgs
    and leaves A and its facility as they are."""
    a = Organization(name="Hospital A", type=OrgType.HOSPITAL, lat=1.0, lng=1.0)
    session.add(a)
    await session.flush()
    session.add(Facility(org_id=a.id, name="old", address="old", lat=1, lng=1,
                         has_cold_storage=True))  # fmt: skip
    await session.flush()
    created = await seed.seed(session, MORNING)
    assert "Hospital A" not in created and len(created) == len(seed.ORGS) - 1
    rows = await snapshot(session)
    assert ("Hospital A", "old", True, 1, 1) in rows["facilities"]
    assert ("Hospital A", OrgType.HOSPITAL, OrgStatus.ACTIVE, 1.0, 1.0) in rows["orgs"]


def km(a: Organization, b: Organization) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a.lat, a.lng, b.lat, b.lng))
    h = (
        math.sin((la2 - la1) / 2) ** 2
        + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    )
    return 2 * 6371 * math.asin(math.sqrt(h))


async def test_hospitals_are_5_to_40_km_apart(session: AsyncSession) -> None:
    await seed.seed(session, MORNING)
    hospitals = list(
        await session.scalars(select(Organization).where(Organization.type == OrgType.HOSPITAL))
    )
    distances = [km(a, b) for a in hospitals for b in hospitals if a.name < b.name]
    assert len(distances) == 15
    assert all(5 <= d <= 40 for d in distances), distances


async def test_seeded_users_sign_in_with_the_demo_password(
    session: AsyncSession, client_for: ClientFor
) -> None:
    await seed.seed(session, MORNING)
    client = await client_for()
    for email in ("approver@hospital-a.demo", "admin@care-e.demo", "supplier.desk@supplier-z.demo"):
        r = await client.post("/auth/login", json={"email": email, "password": seed.PASSWORD})
        assert r.status_code == 200, r.text


async def report(
    session: AsyncSession,
    hospital: str,
    code: str,
    *,
    required: int,
    usable: int,
    priority: Priority,
    now: datetime,
    hours: int,
    min_shelf_life_days: int | None = None,
) -> tuple[Shortage, dict[str, CandidateOut]]:
    org = await session.scalar(select(Organization).where(Organization.name == hospital))
    assert org is not None
    user = await session.scalar(
        select(User).where(User.email == f"store.manager@{hospital.lower().replace(' ', '-')}.demo")
    )
    facility = await session.scalar(select(Facility).where(Facility.org_id == org.id))
    product = await session.scalar(select(Product).where(Product.code == code))
    assert user is not None and facility is not None and product is not None
    body = ShortageCreate(
        facility_id=facility.id,
        product_id=product.id,
        qty_required=required,
        qty_local_usable=usable,
        required_by=now + timedelta(hours=hours),
        priority=priority,
        min_shelf_life_days=min_shelf_life_days,
    )
    shortage = await shortages.create_shortage(session, user, body, now=now)
    run = await shortages.latest_run(session, shortage.id)
    assert run is not None
    out = await shortages.match_run_out(session, run)
    return shortage, {c.source_org_name: c for c in out.candidates}


def failed(c: CandidateOut) -> list[str | None]:
    return [g.reason for g in c.gate_results if not g.passed]


@pytest.mark.parametrize("now", [MORNING, LATE], ids=["06:00 UTC", "23:30 UTC"])
async def test_scenario_1_figures_hold_at_any_time_of_day(
    session: AsyncSession, now: datetime
) -> None:
    await seed.seed(session, now)
    shortage, c = await report(
        session, "Hospital A", "SURG-KIT-A", required=1000, usable=150,
        priority=Priority.CRITICAL, now=now, hours=72, min_shelf_life_days=30,
    )  # fmt: skip
    assert shortage.shortfall == 850
    assert set(c) == {f"Hospital {x}" for x in "BCDE"} | {"Supplier X", "Supplier Y"}
    assert (c["Hospital B"].transferable_qty, c["Hospital B"].rank, failed(c["Hospital B"])) == (
        1000,
        1,
        [],
    )
    assert failed(c["Hospital C"]) == ["Only 100 transferable; 850 needed"]
    assert failed(c["Hospital D"]) == ["Expires in 12 days; 30 required"]
    assert c["Hospital D"].transferable_qty == 900
    assert failed(c["Hospital E"]) == ["Not authorized to supply this product"]
    assert c["Hospital E"].transferable_qty == 1200
    x, y = c["Supplier X"], c["Supplier Y"]
    assert (x.eligible, y.eligible, y.rank, x.rank) == (True, True, 2, 3)
    assert x.eta_hours == pytest.approx(68, abs=1)
    assert y.eta_hours == pytest.approx(24, abs=1)
    assert x.landed_cost_paise == pytest.approx(1_200_000, rel=0.05)
    assert y.landed_cost_paise == pytest.approx(2_400_000, rel=0.05)


async def test_scenario_2_recommends_hospital_f(session: AsyncSession) -> None:
    await seed.seed(session, MORNING)
    _, c = await report(
        session, "Hospital C", "DIAG-RDK", required=200, usable=0,
        priority=Priority.ROUTINE, now=MORNING, hours=48, min_shelf_life_days=60,
    )  # fmt: skip
    f = c["Hospital F"]
    assert (f.transferable_qty, f.eligible, f.rank) == (500, True, 1)
    for name in ("Hospital A", "Hospital B", "Hospital D", "Hospital E"):
        assert failed(c[name]) == ["No cold storage at the source facility"]


async def test_scenario_3_ranks_hospital_b_first(session: AsyncSession) -> None:
    await seed.seed(session, MORNING)
    shortage, c = await report(
        session, "Hospital E", "IV-CAN-20G", required=300, usable=0,
        priority=Priority.ROUTINE, now=MORNING, hours=48,
    )  # fmt: skip
    b = c["Hospital B"]
    assert (b.transferable_qty, b.eligible, b.rank) == (800, True, 1)
    run = await shortages.latest_run(session, shortage.id)
    assert run is not None and run.planned_resolution is not None
    assert run.planned_resolution["type"] == "TRANSFER"
    assert [x["source_org_id"] for x in run.planned_resolution["lines"]] == [str(b.source_org_id)]
