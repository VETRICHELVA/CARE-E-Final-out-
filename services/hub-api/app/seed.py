"""Minimal dev seed (S02): platform admin, Hospital A and Hospital B with one user per role.
The full demo seed replaces this in S20. Run with `make seed`; safe to run twice."""

import asyncio
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Role, User
from app.auth.service import hash_password
from app.db import SessionLocal
from app.orgs.models import Facility, Organization, OrgType

PASSWORD = os.environ.get("SEED_PASSWORD", "care-e-dev")
HOSPITAL_ROLES = ["STORE_MANAGER", "REQUESTER", "APPROVER", "RECEIVER", "ADMIN"]

# name, type, email domain, lat, lng, roles
ORGS = [
    ("CARE-E Platform", OrgType.PLATFORM, "care-e.local", 12.9716, 77.5946, ["ADMIN"]),
    ("Hospital A", OrgType.HOSPITAL, "hospital-a.local", 12.9592, 77.6974, HOSPITAL_ROLES),
    ("Hospital B", OrgType.HOSPITAL, "hospital-b.local", 12.9279, 77.6271, HOSPITAL_ROLES),
]


async def seed(session: AsyncSession) -> bool:
    """Insert the seed orgs and users unless the platform org already exists."""
    if await session.scalar(select(Organization).where(Organization.type == OrgType.PLATFORM)):
        return False
    roles = {r.name: r for r in await session.scalars(select(Role))}
    password_hash = hash_password(PASSWORD)
    for name, org_type, domain, lat, lng, role_names in ORGS:
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
    return True


async def main() -> None:
    async with SessionLocal() as session:
        created = await seed(session)
        await session.commit()
    print("Seeded dev data." if created else "Seed data already present; nothing to do.")
    if created:
        print("Sign in as e.g. approver@hospital-a.local; password: $SEED_PASSWORD or care-e-dev")


if __name__ == "__main__":
    asyncio.run(main())
