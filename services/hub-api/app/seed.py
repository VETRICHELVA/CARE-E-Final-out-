"""Minimal dev seed: platform admin, Hospital A and B, Supplier X and SwiftMed Logistics with
one user per role (S02, S04), the product catalog (S04), SwiftMed's cold box `cb-01` (S14)
and SwiftMed's drivers Ravi and Priya and its two vehicles, one cold-chain (S11).
The full demo seed replaces this in S20. Run with `make seed`; idempotent per org (and per
device), so an older seeded DB gains new orgs."""

import asyncio
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Role, User
from app.auth.service import hash_password
from app.catalog.service import seed_catalog
from app.db import SessionLocal
from app.iot.models import Device
from app.orgs.models import Facility, Organization, OrgType
from app.shipments.models import Driver, Vehicle

PASSWORD = os.environ.get("SEED_PASSWORD", "demo1234")
HOSPITAL_ROLES = ["STORE_MANAGER", "REQUESTER", "APPROVER", "RECEIVER", "ADMIN"]
SUPPLIER_ROLES = ["SUPPLIER_DESK", "ADMIN"]
LOGISTICS_ROLES = ["DISPATCHER", "DRIVER", "ADMIN"]

# name, type, email domain, lat, lng, roles
ORGS = [
    ("CARE-E Platform", OrgType.PLATFORM, "care-e.demo", 12.9716, 77.5946, ["ADMIN"]),
    ("Hospital A", OrgType.HOSPITAL, "hospital-a.demo", 12.9592, 77.6974, HOSPITAL_ROLES),
    ("Hospital B", OrgType.HOSPITAL, "hospital-b.demo", 12.9279, 77.6271, HOSPITAL_ROLES),
    ("Supplier X", OrgType.SUPPLIER, "supplier-x.demo", 13.0358, 77.5970, SUPPLIER_ROLES),
    ("SwiftMed Logistics", OrgType.LOGISTICS, "swiftmed.demo", 12.9784, 77.6408, LOGISTICS_ROLES),
]

# device_id, owning org: the simulator's default device (scripts/simulate_telemetry.py)
DEVICES = [("cb-01", "SwiftMed Logistics")]

# SwiftMed Logistics' fleet (demo-scenarios.md): 2 drivers (Ravi, Priya), 2 vehicles (one
# cold-chain). Ravi is the org's DRIVER user `driver@swiftmed.demo`; Priya gets
# `driver2@swiftmed.demo`. Phone numbers are placeholders.
FLEET_ORG = "SwiftMed Logistics"
DRIVERS = [
    ("Ravi", "driver@swiftmed.demo", "+91 90000 00001"),
    ("Priya", "driver2@swiftmed.demo", "+91 90000 00002"),
]
VEHICLES = [("KA-01-SM-0001", False), ("KA-01-SM-0002", True)]  # reg_no, has_cold_chain


async def seed(session: AsyncSession) -> list[str]:
    """Insert each seed org (with its users) not present yet, by name; load the catalog.
    Returns the names of the orgs created."""
    existing = set(await session.scalars(select(Organization.name)))
    missing = [o for o in ORGS if o[0] not in existing]
    roles = {r.name: r for r in await session.scalars(select(Role))}
    password_hash = hash_password(PASSWORD) if missing else ""
    for name, org_type, domain, lat, lng, role_names in missing:
        org = Organization(name=name, type=org_type, lat=lat, lng=lng)
        session.add(org)
        await session.flush()
        if org_type == OrgType.HOSPITAL:
            session.add(
                Facility(
                    org_id=org.id,
                    name=f"{name} central store",
                    address=f"{name}, Bengaluru",
                    lat=lat,
                    lng=lng,
                    has_cold_storage=True,
                )
            )
        for role in role_names:
            session.add(
                User(
                    email=f"{role.lower().replace('_', '.')}@{domain}",
                    password_hash=password_hash,
                    full_name=f"{name} {role.replace('_', ' ').title()}",
                    org_id=org.id,
                    roles=[roles[role]],
                )
            )
    await session.flush()
    await seed_catalog(session)
    await seed_devices(session)
    await seed_fleet(session)
    return [o[0] for o in missing]


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


async def main() -> None:
    async with SessionLocal() as session:
        created = await seed(session)
        await session.commit()
    print(f"Seeded: {', '.join(created)}." if created else "Seed orgs already present.")
    print("Catalog loaded from scripts/seed/catalog.py; cold box cb-01 registered.")
    print("SwiftMed fleet: drivers Ravi and Priya, vans KA-01-SM-0001 and KA-01-SM-0002 (cold).")
    print("Sign in as e.g. approver@hospital-a.demo; password: $SEED_PASSWORD or demo1234")


if __name__ == "__main__":
    asyncio.run(main())
