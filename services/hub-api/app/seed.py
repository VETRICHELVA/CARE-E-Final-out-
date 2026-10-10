"""The demo seed (S20): every org, user, product, authorization, batch, offer, vehicle, driver,
device and the synthetic consumption history in docs/specs/demo-scenarios.md, with every time
relative to `now`. Run with `make seed` (or `make demo-reset` for an empty database first).

Create-only: a run adds what is missing and never changes what exists (CLAUDE.md rule 5: a
change to stock, an offer, an org or an authorization outside the hub's own services would have
no audit row). Orgs, facilities, users, authorizations, batches (by facility, product and
batch_no), offers, drivers, vehicles and devices are added if missing, with the scenario
numbers and times relative to `now`; existing rows keep their quantities, prices, statuses and
`last_verified_at`. Only the synthetic consumption history (not stock; reported rows win) is
regenerated. To put every figure back to demo-scenarios.md, run `make demo-reset`, which drops
the database first.

Scenario numbers (demo-scenarios.md):
- Scenario 1, Surgical Kit A: B 1,000 transferable (verified 2 h ago), C 100 (3 h), D 900
  expiring so that the shelf-life gate reads "Expires in 12 days" when the seed runs, whatever
  the time of day (see `pinned_expiry`), E 1,200 (not authorized), X and Y offers, no Z offer.
  Hospital A holds the 150 it reports as usable.
- Scenario 2, Rapid Diagnostic Kit: F 500 transferable (+240 days, verified now), cold storage
  at C and F only; C holds none.
- Scenario 3, IV Cannula 20G: B one batch (1,000 on hand, safety 200, +55 days, bought cheaper
  than anyone else's), E 120 usable; the consumption series come from
  scripts/seed/consumption.py.
- Every other hospital x top-15 product gets one batch whose forecast usage before expiry lies
  between on_hand - safety_stock and on_hand, so the forecast shows neither an expiry-risk
  excess nor a stock-out for it (only the scenarios' own figures stand out).
- Suppliers X, Y and Z offer every product except the e2e tests' own (E2E_PRODUCTS) and Z's
  Surgical Kit A.

Logins: `<role>@<org>.demo` (e.g. approver@hospital-a.demo), password $SEED_PASSWORD or
demo1234 (development only). SwiftMed's second driver, Priya, is driver2@swiftmed.demo."""

import argparse
import asyncio
import os
import runpy
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import routing
from app.auth.models import Role, User
from app.auth.service import hash_password
from app.catalog.models import Product, ProductAuthorization, SupplierOffer
from app.catalog.service import seed_catalog
from app.db import SessionLocal
from app.domain.costing import Point, transport_eta_hours
from app.inventory.models import InventoryBatch
from app.iot.models import Device
from app.orgs.models import Facility, Organization, OrgStatus, OrgType
from app.shipments.models import Driver, Vehicle

PASSWORD = os.environ.get("SEED_PASSWORD", "demo1234")
SEED_DIR = Path(__file__).resolve().parents[3] / "scripts" / "seed"
HOSPITAL_ROLES = ["STORE_MANAGER", "REQUESTER", "APPROVER", "RECEIVER", "ADMIN"]
SUPPLIER_ROLES = ["SUPPLIER_DESK", "ADMIN"]
LOGISTICS_ROLES = ["DISPATCHER", "DRIVER", "ADMIN"]

# name, type, email domain, lat, lng, roles. Hospitals are 5-40 km apart in Bengaluru.
ORGS = [
    ("CARE-E Platform", OrgType.PLATFORM, "care-e.demo", 12.9716, 77.5946, ["ADMIN"]),
    ("Hospital A", OrgType.HOSPITAL, "hospital-a.demo", 12.9592, 77.6974, HOSPITAL_ROLES),
    ("Hospital B", OrgType.HOSPITAL, "hospital-b.demo", 12.9279, 77.6271, HOSPITAL_ROLES),
    ("Supplier X", OrgType.SUPPLIER, "supplier-x.demo", 13.0358, 77.5970, SUPPLIER_ROLES),
    ("SwiftMed Logistics", OrgType.LOGISTICS, "swiftmed.demo", 12.9784, 77.6408, LOGISTICS_ROLES),
    ("Hospital C", OrgType.HOSPITAL, "hospital-c.demo", 13.0100, 77.6500, HOSPITAL_ROLES),
    ("Hospital D", OrgType.HOSPITAL, "hospital-d.demo", 12.8900, 77.6800, HOSPITAL_ROLES),
    ("Hospital E", OrgType.HOSPITAL, "hospital-e.demo", 13.0000, 77.7400, HOSPITAL_ROLES),
    ("Hospital F", OrgType.HOSPITAL, "hospital-f.demo", 12.9716, 77.6000, HOSPITAL_ROLES),
    ("Supplier Y", OrgType.SUPPLIER, "supplier-y.demo", 12.8500, 77.6600, SUPPLIER_ROLES),
    ("Supplier Z", OrgType.SUPPLIER, "supplier-z.demo", 13.0500, 77.7000, SUPPLIER_ROLES),
]
COLD_STORAGE = {"Hospital C", "Hospital F"}

# device_id, owning org: the simulator's default device (scripts/simulate_telemetry.py)
DEVICES = [("cb-01", "SwiftMed Logistics")]

# SwiftMed Logistics' fleet: 2 drivers (Ravi, Priya), 2 vehicles (one cold-chain). Ravi is the
# org's DRIVER user `driver@swiftmed.demo`; Priya gets `driver2@swiftmed.demo`. Phone numbers
# are placeholders.
FLEET_ORG = "SwiftMed Logistics"
DRIVERS = [
    ("Ravi", "driver@swiftmed.demo", "+91 90000 00001"),
    ("Priya", "driver2@swiftmed.demo", "+91 90000 00002"),
]
VEHICLES = [("KA-01-SM-0001", False), ("KA-01-SM-0002", True)]  # reg_no, has_cold_chain

# The e2e tests' own products (e2e/seed/*.py): each test gives Supplier Y the only offer, so the
# demo seed stocks and offers none of them.
E2E_PRODUCTS = {"SURG-DRP-STR", "PPE-FSH", "RSP-NEB-MSK"}

# Unit price in paise a supplier charges at factor 1.0. Hospitals bought at HOSPITAL_COST of it.
BASE_PRICE = {
    "SURG-KIT-A": 1_500,
    "SURG-GLV-STR": 3_500,
    "SURG-BLD-22": 45_000,
    "SURG-SUT-ABS": 120_000,
    "SURG-DRP-STR": 9_000,
    "DIAG-RDK": 35_000,
    "DIAG-GLU-STR": 60_000,
    "DIAG-URN-DIP": 40_000,
    "DIAG-ECG-ELC": 25_000,
    "DIAG-EDTA-TUB": 80_000,
    "DIAG-BLD-CUL": 30_000,
    "IV-CAN-20G": 800,
    "IV-SET-INF": 1_800,
    "IV-NS-500": 2_500,
    "IV-RL-500": 3_000,
    "IV-STC-3W": 1_200,
    "INJ-SYR-5": 300,
    "INJ-NDL-HYP": 25_000,
    "INJ-SYR-INS": 600,
    "INJ-NDL-SPN": 15_000,
    "PPE-N95": 4_500,
    "PPE-MSK-3PLY": 20_000,
    "PPE-GLV-NIT": 35_000,
    "PPE-GWN-ISO": 9_000,
    "PPE-FSH": 6_500,
    "WND-GZE-STR": 4_000,
    "WND-BND-CRP": 3_500,
    "WND-TAP-MIC": 2_500,
    "WND-DRS-FOAM": 18_000,
    "RSP-O2-MSK": 6_000,
    "RSP-NSL-PRG": 3_500,
    "RSP-ETT-75": 9_500,
    "RSP-NEB-MSK": 7_500,
    "CTH-FOL-16": 8_000,
    "CTH-URB-2L": 5_500,
    "MED-INS-REG": 17_000,
    "MED-OXY-5IU": 1_500,
    "MED-TT-VAC": 2_500,
    "MED-HEPB-VAC": 9_000,
    "MED-ARV-VAC": 32_000,
}
HOSPITAL_COST_PCT = 85  # hospitals buy below list price ...
CHEAP_COST_PCT = 70  # ... and cheaper still for Scenario 3's overstock at Hospital B

# supplier: price factor (%), lead time in hours, available qty
SUPPLIER_TERMS = {
    "Supplier X": (100, 48, 5_000),
    "Supplier Y": (110, 24, 3_000),
    "Supplier Z": (97, 72, 8_000),
}
# Scenario 1: (unit price paise, lead time hours, available); Supplier Z offers no Surgical Kit A.
SCENARIO_1_OFFERS = {"Supplier X": (1_400, 66, 5_000), "Supplier Y": (2_800, 22, 2_000)}

# Default batches: expiry in DEFAULT_EXPIRY_DAYS (+ a per-product spread), on hand = daily use x
# (expiry days + MARGIN), safety stock = daily use x 2 x MARGIN: forecast usage before expiry then
# sits MARGIN days of use inside both bounds (no expiry-risk excess, no stock-out).
DEFAULT_EXPIRY_DAYS = 100
MARGIN_DAYS = 10
DEFAULT_VERIFIED_AGO = timedelta(hours=6)  # inside the CRITICAL 24 h freshness gate
SEED_BATCH = "SEED-1"
# pg advisory lock: seeds run one at a time. Playwright runs spec files in parallel and each
# e2e seed (e2e/seed/*.py) calls `seed` first, so without it two runs could both find a user
# or org missing and both insert it (a unique-violation crash).
SEED_LOCK = 0x5EED_0020


@dataclass(frozen=True)
class BatchSpec:
    """One seeded batch; `expiry` is a date, `verified_ago` how long before `now` it was
    counted."""

    hospital: str
    code: str
    on_hand: int
    expiry: date
    unit_cost_paise: int
    verified_ago: timedelta
    reserved: int = 0
    allocated: int = 0
    safety_stock: int = 0
    batch_no: str = SEED_BATCH


def consumption() -> dict[str, Any]:
    """scripts/seed/consumption.py's generator and rates (also loaded by S18's tests)."""
    return runpy.run_path(str(SEED_DIR / "consumption.py"))


def location(name: str) -> Point:
    for org_name, _, _, lat, lng, _ in ORGS:
        if org_name == name:
            return Point(lat, lng)
    raise KeyError(name)


async def pinned_expiry(now: datetime, source: str, dest: str, days: int) -> date:
    """An expiry date the shelf-life gate reads as exactly `days` for a shortage at `dest`
    matched at `now`: the gate counts days from the delivery date (now + transport ETA, UTC;
    business-rules.md §2), so late in the UTC day the batch must expire a day later than
    `now.date() + days` (the S05 -> S20 follow-up). Uses the same routing as matching."""
    km = await routing.ROUTING.distance_km(location(source), location(dest))
    arrival = (now + timedelta(hours=transport_eta_hours(km))).date()
    return arrival + timedelta(days=days)


async def batch_specs(now: datetime) -> list[BatchSpec]:
    today = now.date()
    gen = consumption()
    rates: dict[str, int] = gen["TOP_PRODUCTS"]
    sizes: dict[str, float] = gen["HOSPITAL_SIZES"]
    not_stocked: set[tuple[str, str]] = gen["NOT_STOCKED"]

    def cost(code: str, pct: int = HOSPITAL_COST_PCT) -> int:
        return BASE_PRICE[code] * pct // 100

    def hours(n: float) -> timedelta:
        return timedelta(hours=n)

    scenario = [
        # Scenario 1: Surgical Kit A (demo-scenarios.md table)
        BatchSpec("Hospital A", "SURG-KIT-A", 150, today + timedelta(100), cost("SURG-KIT-A"),
                  hours(1)),
        BatchSpec("Hospital B", "SURG-KIT-A", 2_500, today + timedelta(180), cost("SURG-KIT-A"),
                  hours(2), reserved=800, allocated=200, safety_stock=500),
        BatchSpec("Hospital C", "SURG-KIT-A", 1_400, today + timedelta(200), cost("SURG-KIT-A"),
                  hours(3), reserved=800, safety_stock=500),
        BatchSpec("Hospital D", "SURG-KIT-A", 900,
                  await pinned_expiry(now, "Hospital D", "Hospital A", 12), cost("SURG-KIT-A"),
                  hours(1)),
        BatchSpec("Hospital E", "SURG-KIT-A", 1_200, today + timedelta(150), cost("SURG-KIT-A"),
                  hours(1)),
        # Scenario 2: Rapid Diagnostic Kit, 500 transferable at F, verified today; C has none.
        BatchSpec("Hospital F", "DIAG-RDK", 600, today + timedelta(240), cost("DIAG-RDK"),
                  timedelta(0), safety_stock=100),
        # Scenario 3: IV Cannula 20G
        BatchSpec("Hospital B", "IV-CAN-20G", 1_000, today + timedelta(55),
                  cost("IV-CAN-20G", CHEAP_COST_PCT), hours(1), safety_stock=200),
        BatchSpec("Hospital E", "IV-CAN-20G", 120, today + timedelta(200), cost("IV-CAN-20G"),
                  hours(1)),
    ]  # fmt: skip
    covered = {(s.hospital, s.code) for s in scenario} | {("Hospital C", "DIAG-RDK")}
    defaults = []
    for n, (code, rate) in enumerate(rates.items()):
        for hospital, size in sizes.items():
            if (hospital, code) in covered or (hospital, code) in not_stocked:
                continue
            daily = rate * size
            days = DEFAULT_EXPIRY_DAYS + 4 * n  # 100-156 days: never "near expiry" (90)
            defaults.append(
                BatchSpec(
                    hospital,
                    code,
                    on_hand=round(daily * (days + MARGIN_DAYS)),
                    expiry=today + timedelta(days),
                    unit_cost_paise=cost(code),
                    verified_ago=DEFAULT_VERIFIED_AGO,
                    safety_stock=round(daily * 2 * MARGIN_DAYS),
                )
            )
    return scenario + defaults


def offer_specs() -> dict[tuple[str, str], tuple[int, int, int]]:
    """(supplier, product code) -> (unit price paise, lead time hours, available qty)."""
    offers = {}
    for supplier, (pct, lead, qty) in SUPPLIER_TERMS.items():
        for code, price in BASE_PRICE.items():
            if code not in E2E_PRODUCTS and code != "SURG-KIT-A":
                offers[supplier, code] = (price * pct // 100, lead, qty)
    for supplier, terms in SCENARIO_1_OFFERS.items():
        offers[supplier, "SURG-KIT-A"] = terms
    return offers


async def seed(session: AsyncSession, now: datetime | None = None) -> list[str]:
    """Put the demo data in place (everything but the consumption history and forecasts,
    which `main` adds). Returns the names of the orgs created by this run."""
    now = now or datetime.now(UTC)
    # Held until the caller's transaction ends, so the get-or-create below cannot race.
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": SEED_LOCK})
    created = await seed_orgs(session)
    products = await seed_catalog(session)
    orgs = {o.name: o for o in await session.scalars(select(Organization))}
    await seed_authorizations(session, orgs, products)
    await seed_batches(session, orgs, products, now)
    await seed_offers(session, orgs, products, now)
    await seed_devices(session)
    await seed_fleet(session)
    return created


async def seed_orgs(session: AsyncSession) -> list[str]:
    """Each org (by name) with its facility (hospitals) and one user per role (by email)."""
    roles = {r.name: r for r in await session.scalars(select(Role))}
    existing = {o.name: o for o in await session.scalars(select(Organization))}
    emails = set(await session.scalars(select(User.email)))
    password_hash = ""
    created = []
    for name, org_type, domain, lat, lng, role_names in ORGS:
        org = existing.get(name)
        if org is None:
            org = Organization(name=name, type=org_type, lat=lat, lng=lng, status=OrgStatus.ACTIVE)
            session.add(org)
            await session.flush()
            created.append(name)
        if org_type == OrgType.HOSPITAL:
            facility = await session.scalar(
                select(Facility).where(Facility.org_id == org.id).order_by(Facility.created_at)
            )
            if facility is None:
                session.add(
                    Facility(
                        org_id=org.id,
                        name=f"{name} central store",
                        address=f"{name}, Bengaluru",
                        lat=lat,
                        lng=lng,
                        has_cold_storage=name in COLD_STORAGE,
                    )
                )
        for role in role_names:
            email = f"{role.lower().replace('_', '.')}@{domain}"
            if email in emails:
                continue
            password_hash = password_hash or hash_password(PASSWORD)  # argon2: hash once
            session.add(
                User(
                    email=email,
                    password_hash=password_hash,
                    full_name=f"{name} {role.replace('_', ' ').title()}",
                    org_id=org.id,
                    roles=[roles[role]],
                )
            )
    await session.flush()
    return created


async def seed_authorizations(
    session: AsyncSession, orgs: dict[str, Organization], products: dict[str, Product]
) -> None:
    """Every hospital and supplier for every product, except Hospital E for Surgical Kit A;
    only the missing ones are added (an existing grant or its absence is left as it is)."""
    wanted = {
        (org.id, product.id)
        for org in orgs.values()
        if org.name in {o[0] for o in ORGS} and org.type in (OrgType.HOSPITAL, OrgType.SUPPLIER)
        for product in products.values()
    }
    wanted.discard((orgs["Hospital E"].id, products["SURG-KIT-A"].id))
    have = set(
        await session.execute(select(ProductAuthorization.org_id, ProductAuthorization.product_id))
    )
    session.add_all(
        ProductAuthorization(org_id=o, product_id=p) for o, p in sorted(wanted - set(have))
    )
    await session.flush()


async def seed_batches(
    session: AsyncSession,
    orgs: dict[str, Organization],
    products: dict[str, Product],
    now: datetime,
) -> None:
    """Each seeded batch that is missing (by facility, product and batch_no); an existing
    batch is never touched, whatever its quantities or `last_verified_at`."""
    facilities = {
        f.org_id: f
        for f in await session.scalars(select(Facility).order_by(Facility.created_at.desc()))
    }
    for spec in await batch_specs(now):
        org, product = orgs[spec.hospital], products[spec.code]
        facility = facilities[org.id]
        batch = await session.scalar(
            select(InventoryBatch).where(
                InventoryBatch.facility_id == facility.id,
                InventoryBatch.product_id == product.id,
                InventoryBatch.batch_no == spec.batch_no,
            )
        )
        if batch is not None:
            continue  # stock changes only through the hub's services, with audit rows
        session.add(
            InventoryBatch(
                org_id=org.id, facility_id=facility.id, product_id=product.id,
                batch_no=spec.batch_no, on_hand=spec.on_hand, reserved=spec.reserved,
                allocated=spec.allocated, safety_stock=spec.safety_stock, quarantined=0,
                expiry_date=spec.expiry, unit_cost_paise=spec.unit_cost_paise,
                last_verified_at=now - spec.verified_ago,
            )
        )  # fmt: skip
    await session.flush()


async def seed_offers(
    session: AsyncSession,
    orgs: dict[str, Organization],
    products: dict[str, Product],
    now: datetime,
) -> None:
    """The offers in `offer_specs` that are missing, updated `now` (Z offers no Surgical Kit
    A); an existing offer keeps its price, lead time, quantity and time."""
    existing = set(await session.execute(select(SupplierOffer.org_id, SupplierOffer.product_id)))
    for (supplier, code), (price, lead, qty) in offer_specs().items():
        key = (orgs[supplier].id, products[code].id)
        if key in existing:
            continue
        session.add(
            SupplierOffer(
                org_id=key[0], product_id=key[1], unit_price_paise=price, lead_time_hours=lead,
                available_qty=qty, updated_at=now,
            )
        )  # fmt: skip
    await session.flush()


async def seed_fleet(session: AsyncSession) -> None:
    """SwiftMed's drivers and vehicles, each added only if missing (by email / reg_no)."""
    org = await session.scalar(select(Organization).where(Organization.name == FLEET_ORG))
    if org is None:
        return
    driver_role = await session.scalar(select(Role).where(Role.name == "DRIVER"))
    assert driver_role is not None
    for name, email, phone in DRIVERS:
        user = await session.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(
                email=email,
                password_hash=hash_password(PASSWORD),
                full_name=name,
                org_id=org.id,
                roles=[driver_role],
            )
            session.add(user)
            await session.flush()
        if await session.scalar(select(Driver).where(Driver.user_id == user.id)) is None:
            user.full_name = name
            session.add(Driver(org_id=org.id, user_id=user.id, phone=phone))
    existing = set(await session.scalars(select(Vehicle.reg_no).where(Vehicle.org_id == org.id)))
    for reg_no, cold in VEHICLES:
        if reg_no not in existing:
            session.add(Vehicle(org_id=org.id, reg_no=reg_no, has_cold_chain=cold))
    await session.flush()


async def seed_devices(session: AsyncSession) -> None:
    """Register each seed device not present yet, by device_id."""
    existing = set(await session.scalars(select(Device.device_id)))
    orgs = await session.execute(select(Organization.name, Organization.id))
    org_ids = {name: org_id for name, org_id in orgs}
    for device_id, org_name in DEVICES:
        if device_id not in existing and org_name in org_ids:
            session.add(Device(org_id=org_ids[org_name], device_id=device_id))
    await session.flush()


async def main(forecast: bool = True) -> None:
    from app.forecasting import service as forecasting

    now = datetime.now(UTC)
    async with SessionLocal() as session:
        created = await seed(session, now)
        counts = await consumption()["load"](session, now.date())
        await session.commit()
        print(f"Created: {', '.join(created)}." if created else "Every seed org was present.")
        print("Orgs, users, catalog, authorizations, batches, offers, fleet and cb-01 in place.")
        print(f"Synthetic consumption: {sum(counts.values())} records for {len(counts)} hospitals.")
        if forecast:
            summary = await forecasting.run(
                session, await forecasting.hospital_org_ids(session), forecasting.today()
            )
            print(f"Forecasts: {summary.series} series for {len(summary.org_ids)} hospitals.")
    print("Sign in as e.g. approver@hospital-a.demo; password: $SEED_PASSWORD or demo1234")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--no-forecast", action="store_true", help="skip the forecast run (statsmodels)"
    )
    asyncio.run(main(forecast=not parser.parse_args().no_forecast))
