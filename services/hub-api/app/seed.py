"""Minimal dev seed: platform admin, Hospital A and B, Supplier X and SwiftMed Logistics with
one user per role (S02, S04), plus the product catalog (S04). The full demo seed replaces this
in S20. Run with `make seed`; idempotent per org, so an older seeded DB gains new orgs."""

import asyncio
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Role, User
from app.auth.service import hash_password
from app.catalog.service import seed_catalog
from app.db import SessionLocal
from app.orgs.models import Facility, Organization, OrgType

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
    return [o[0] for o in missing]


async def main() -> None:
    async with SessionLocal() as session:
        created = await seed(session)
        await session.commit()
    print(f"Seeded: {', '.join(created)}." if created else "Seed orgs already present.")
    print("Catalog loaded from scripts/seed/catalog.py.")
    print("Sign in as e.g. approver@hospital-a.demo; password: $SEED_PASSWORD or demo1234")


if __name__ == "__main__":
    asyncio.run(main())
