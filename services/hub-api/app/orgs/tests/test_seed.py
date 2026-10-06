import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import seed
from app.auth.models import User
from app.catalog.models import Product
from app.conftest import ClientFor

pytestmark = pytest.mark.anyio

NEW_USERS = {
    "supplier.desk@supplier-x.demo": ("Supplier X", "SUPPLIER", "SUPPLIER_DESK"),
    "admin@supplier-x.demo": ("Supplier X", "SUPPLIER", "ADMIN"),
    "dispatcher@swiftmed.demo": ("SwiftMed Logistics", "LOGISTICS", "DISPATCHER"),
    "driver@swiftmed.demo": ("SwiftMed Logistics", "LOGISTICS", "DRIVER"),
    "admin@swiftmed.demo": ("SwiftMed Logistics", "LOGISTICS", "ADMIN"),
}


async def test_seed_adds_missing_orgs_to_an_older_seed(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, client_for: ClientFor
) -> None:
    with monkeypatch.context() as m:  # a DB seeded by S02: platform, Hospital A and B only
        m.setattr(seed, "ORGS", seed.ORGS[:3])
        assert await seed.seed(session) == ["CARE-E Platform", "Hospital A", "Hospital B"]

    assert await seed.seed(session) == ["Supplier X", "SwiftMed Logistics"]
    assert await seed.seed(session) == []

    users = {u.email: u for u in await session.scalars(select(User))}
    assert len(users) == 1 + 5 + 5 + 2 + 3
    for email, (org, org_type, role) in NEW_USERS.items():
        u = users[email]
        assert (u.org.name, u.org.type, [r.name for r in u.roles]) == (org, org_type, [role])
    assert await session.scalar(select(func.count()).select_from(Product)) == 40

    r = await (await client_for()).post(
        "/auth/login", json={"email": "supplier.desk@supplier-x.demo", "password": seed.PASSWORD}
    )
    assert r.status_code == 200
