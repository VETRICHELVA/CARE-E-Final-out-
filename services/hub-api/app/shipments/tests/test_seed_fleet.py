import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import seed
from app.auth.models import User
from app.conftest import ClientFor
from app.shipments.models import Driver, Vehicle
from app.shortages import service as shortages

pytestmark = pytest.mark.anyio


async def test_dev_seed_gives_swiftmed_ravi_priya_and_two_vans_once(
    session: AsyncSession, client_for: ClientFor
) -> None:
    await seed.seed(session)
    await seed.seed(session)

    drivers = list(await session.scalars(select(Driver)))
    vehicles = list(await session.scalars(select(Vehicle)))
    assert len(drivers) == 2 and len(vehicles) == 2
    assert sorted((v.reg_no, v.has_cold_chain) for v in vehicles) == [
        ("KA-01-SM-0001", False),
        ("KA-01-SM-0002", True),
    ]
    assert await shortages.cold_chain_vehicle_exists(session)

    dispatcher = await session.scalar(select(User).where(User.email == "dispatcher@swiftmed.demo"))
    assert dispatcher is not None
    client = await client_for(dispatcher)
    r = await client.get("/drivers")
    assert sorted(d["name"] for d in r.json()["items"]) == ["Priya", "Ravi"]
    r = await client.get("/vehicles")
    assert len(r.json()["items"]) == 2

    # Ravi is SwiftMed's DRIVER user; Priya has her own login.
    for email, name in (("driver@swiftmed.demo", "Ravi"), ("driver2@swiftmed.demo", "Priya")):
        user = await session.scalar(select(User).where(User.email == email))
        assert user is not None
        assert (user.full_name, user.org.name, [r.name for r in user.roles]) == (
            name,
            "SwiftMed Logistics",
            ["DRIVER"],
        )
        r = await (await client_for()).post(
            "/auth/login", json={"email": email, "password": seed.PASSWORD}
        )
        assert r.status_code == 200, r.text
