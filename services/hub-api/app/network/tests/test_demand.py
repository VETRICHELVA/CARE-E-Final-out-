"""GET /network/demand (S10): open shortfall totals per product for the caller's supplier
org's offers; 403 for anyone else; and nothing in the response identifies a hospital."""

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import Product
from app.conftest import ClientFor, World
from app.domain.shortage import Status
from app.orgs.models import Facility, Organization, OrgType
from app.shortages.models import Priority, Shortage, ShortageSource
from app.source_requests.tests.conftest import add_offer, add_org, add_user, facility_of

pytestmark = pytest.mark.anyio

SKA, RDK, CAN = "SURG-KIT-A", "DIAG-RDK", "IV-CAN-20G"


async def add_shortage(
    session: AsyncSession,
    world: World,
    org: Organization,
    product: Product,
    status: Status,
    now: datetime,
    *,
    required: int,
    usable: int = 0,
) -> Shortage:
    """A shortage row in `status`, without running matching (only its totals matter here)."""
    creator = next(u for u in world.users.values() if u.org_id == org.id)
    shortage = Shortage(
        org_id=org.id,
        facility_id=(await facility_of(session, org)).id,
        product_id=product.id,
        qty_required=required,
        qty_local_usable=usable,
        shortfall=max(0, required - usable),
        required_by=now + timedelta(hours=72),
        priority=Priority.ROUTINE,
        min_shelf_life_days=30,
        status=status,
        created_by=creator.id,
        source=ShortageSource.FORM,
    )
    session.add(shortage)
    await session.flush()
    return shortage


@pytest.fixture
async def demand(
    session: AsyncSession, world: World, products: dict[str, Product], now: datetime
) -> list[Shortage]:
    """Supplier S offers SKA and RDK (not CAN). Hospitals A and B have shortages of all three
    in every state; only OPEN, MATCHING and AWAITING_DECISION count."""
    a, b, s = world.hospital_a, world.hospital_b, world.supplier
    ska, rdk, can = products[SKA], products[RDK], products[CAN]
    add_offer(session, s, ska, now, price=2800, lead=22, qty=2000)
    await session.flush()
    add_offer(session, s, rdk, now - timedelta(days=9), price=900, lead=48, qty=50)
    await session.flush()
    rows = [
        await add_shortage(session, world, a, ska, Status.MATCHING, now, required=1000, usable=150),
        await add_shortage(session, world, b, ska, Status.OPEN, now, required=300),
        await add_shortage(session, world, b, ska, Status.AWAITING_DECISION, now, required=100),
    ]
    for status in (
        Status.DRAFT,
        Status.IN_FULFILLMENT,
        Status.RECEIVED,
        Status.RESOLVED,
        Status.PARTIALLY_RESOLVED,
        Status.CANCELLED,
    ):
        rows.append(await add_shortage(session, world, a, ska, status, now, required=7777))
        rows.append(await add_shortage(session, world, b, rdk, status, now, required=7777))
    rows.append(await add_shortage(session, world, a, can, Status.MATCHING, now, required=500))
    return rows


async def test_returns_open_shortfall_per_offered_product(
    world: World, products: dict[str, Product], demand: list[Shortage], client_for: ClientFor
) -> None:
    desk = await client_for(world.users["s.SUPPLIER_DESK"])
    r = await desk.get("/network/demand")
    assert r.status_code == 200, r.text
    body = r.json()
    # 850 (A) + 300 + 100 (B) for SKA; RDK is offered but nothing open; CAN is not offered.
    assert body == {
        "items": [
            {"product_id": str(products[SKA].id), "open_shortfall_qty": 1250},
            {"product_id": str(products[RDK].id), "open_shortfall_qty": 0},
        ],
        "next_cursor": None,
    }
    # The supplier's org admin sees the same.
    admin = await client_for(world.users["s.ADMIN"])
    assert (await admin.get("/network/demand")).json() == body


async def test_pages_through_the_offers(
    world: World, products: dict[str, Product], demand: list[Shortage], client_for: ClientFor
) -> None:
    desk = await client_for(world.users["s.SUPPLIER_DESK"])
    first = (await desk.get("/network/demand?limit=1")).json()
    assert [i["product_id"] for i in first["items"]] == [str(products[SKA].id)]
    assert first["next_cursor"]
    second = (await desk.get(f"/network/demand?limit=1&cursor={first['next_cursor']}")).json()
    assert second == {
        "items": [{"product_id": str(products[RDK].id), "open_shortfall_qty": 0}],
        "next_cursor": None,
    }


async def test_another_supplier_sees_only_its_own_products(
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
    demand: list[Shortage],
    client_for: ClientFor,
    now: datetime,
) -> None:
    x = await add_org(session, "Supplier X", OrgType.SUPPLIER, 13.10, 77.59)
    desk_x = await client_for(await add_user(session, x, "SUPPLIER_DESK", "d@x.test"))
    assert (await desk_x.get("/network/demand")).json() == {"items": [], "next_cursor": None}
    add_offer(session, x, products[CAN], now, price=50, lead=12, qty=10)
    await session.flush()
    assert (await desk_x.get("/network/demand")).json()["items"] == [
        {"product_id": str(products[CAN].id), "open_shortfall_qty": 500}
    ]


@pytest.mark.parametrize("key", ["a.ADMIN", "a.STORE_MANAGER", "b.APPROVER", "p.ADMIN"])
async def test_non_supplier_orgs_get_403(
    world: World, demand: list[Shortage], client_for: ClientFor, key: str
) -> None:
    # a.ADMIN and p.ADMIN hold `po.respond`, but their orgs are not suppliers.
    r = await (await client_for(world.users[key])).get("/network/demand")
    assert (r.status_code, r.json()["code"]) == (403, "forbidden")


async def test_supplier_user_without_po_respond_gets_403(
    world: World, client_for: ClientFor
) -> None:
    r = await (await client_for(world.users["s.DRIVER"])).get("/network/demand")
    assert (r.status_code, r.json()["code"]) == (403, "forbidden")


async def test_needs_sign_in(client_for: ClientFor) -> None:
    assert (await (await client_for()).get("/network/demand")).status_code == 401


async def test_no_hospital_identity_appears_anywhere(
    session: AsyncSession, world: World, demand: list[Shortage], client_for: ClientFor
) -> None:
    desk = await client_for(world.users["s.SUPPLIER_DESK"])
    r = await desk.get("/network/demand")
    assert r.status_code == 200, r.text
    text = r.text.lower()
    hospitals = [world.hospital_a, world.hospital_b]
    facilities = list(
        await session.scalars(
            select(Facility).where(Facility.org_id.in_([h.id for h in hospitals]))
        )
    )
    secrets: set[str] = set()
    for h in hospitals:
        secrets |= {str(h.id), h.id.hex, h.name}
    for f in facilities:
        secrets |= {str(f.id), f.id.hex, f.name, f.address}
    for s in demand:
        secrets |= {str(s.id), s.id.hex, str(s.created_by)}
    for user in world.users.values():
        if user.org_id in {h.id for h in hospitals}:
            secrets |= {str(user.id), user.email, user.full_name}
    leaked = sorted(x for x in secrets if x.lower() in text)
    assert leaked == []
    assert "hospital" not in text
    # Only these fields, so no count, location or org can ride along later unnoticed.
    for item in r.json()["items"]:
        assert set(item) == {"product_id", "open_shortfall_qty"}
        uuid.UUID(item["product_id"])
