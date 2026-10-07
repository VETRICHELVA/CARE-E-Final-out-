import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import seed
from app.auth.models import User
from app.conftest import ClientFor
from app.iot.models import Device

pytestmark = pytest.mark.anyio


async def test_dev_seed_registers_cb01_for_swiftmed_once(
    session: AsyncSession, client_for: ClientFor
) -> None:
    await seed.seed(session)
    await seed.seed(session)

    devices = list(await session.scalars(select(Device)))
    assert [d.device_id for d in devices] == ["cb-01"]

    dispatcher = await session.scalar(select(User).where(User.email == "dispatcher@swiftmed.demo"))
    assert dispatcher is not None
    r = await (await client_for(dispatcher)).get("/devices")
    assert [d["device_id"] for d in r.json()["items"]] == ["cb-01"]
