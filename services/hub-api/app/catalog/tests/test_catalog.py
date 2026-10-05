import uuid
from datetime import UTC, datetime, tzinfo
from typing import Self

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.auth.models import Role, User
from app.catalog import service as catalog_service
from app.catalog.models import Product
from app.catalog.service import seed_catalog
from app.conftest import ClientFor, World, password_hash
from app.orgs.models import Organization, OrgType

pytestmark = pytest.mark.anyio


async def test_seed_catalog_is_idempotent(session: AsyncSession) -> None:
    first = await seed_catalog(session)
    second = await seed_catalog(session)
    assert len(first) == 40
    assert {c: p.id for c, p in first.items()} == {c: p.id for c, p in second.items()}
    assert await session.scalar(select(func.count()).select_from(Product)) == 40


async def test_demo_products(products: dict[str, Product]) -> None:
    def attrs(p: Product) -> tuple[object, ...]:
        cold = (p.requires_cold_chain, p.temp_min_c, p.temp_max_c)
        return (p.name, *cold, p.default_min_shelf_life_days)

    assert attrs(products["SURG-KIT-A"]) == ("Surgical Kit A", False, None, None, 30)
    assert attrs(products["DIAG-RDK"]) == ("Rapid Diagnostic Kit", True, 2.0, 8.0, 60)
    assert attrs(products["IV-CAN-20G"]) == ("IV Cannula 20G", False, None, None, 30)


@pytest.mark.parametrize("word", ["surgical", "rapid", "diagnostic", "cannula", "20g"])
async def test_demo_products_have_no_siblings(products: dict[str, Product], word: str) -> None:
    """The chat-ordering evals assume these words name exactly one product."""
    assert len([p for p in products.values() if word in p.name.lower()]) == 1


async def test_plain_kits_are_exactly_the_two_demo_kits(products: dict[str, Product]) -> None:
    names = {p.name for p in products.values() if "kit" in p.name.lower()}
    assert names == {"Surgical Kit A", "Rapid Diagnostic Kit"}


async def test_any_role_lists_products_paginated(
    client_for: ClientFor, world: World, products: dict[str, Product]
) -> None:
    client = await client_for(world.users["s.DRIVER"])
    first = (await client.get("/products", params={"limit": 25})).json()
    rest = (await client.get("/products", params={"cursor": first["next_cursor"]})).json()
    assert (len(first["items"]), len(rest["items"]), rest["next_cursor"]) == (25, 15, None)
    codes = {p["code"] for p in first["items"] + rest["items"]}
    assert codes == set(products)


async def test_get_product(
    client_for: ClientFor, world: World, products: dict[str, Product]
) -> None:
    client = await client_for(world.users["b.APPROVER"])
    r = await client.get(f"/products/{products['DIAG-RDK'].id}")
    assert r.status_code == 200
    assert r.json()["code"] == "DIAG-RDK"
    assert (await client.get(f"/products/{uuid.uuid4()}")).status_code == 404


async def test_products_need_sign_in(client_for: ClientFor, products: dict[str, Product]) -> None:
    assert (await (await client_for()).get("/products")).status_code == 401


# --- supplier offers ---------------------------------------------------------------------


def offer(
    product: Product, price: int = 1400, lead: int = 66, qty: int = 5000
) -> dict[str, object]:
    return {
        "product_id": str(product.id),
        "unit_price_paise": price,
        "lead_time_hours": lead,
        "available_qty": qty,
    }


async def audit_rows(session: AsyncSession, entity_id: str) -> list[AuditLog]:
    stmt = select(AuditLog).where(AuditLog.entity_id == uuid.UUID(entity_id)).order_by(AuditLog.ts)
    return list(await session.scalars(stmt))


async def test_supplier_creates_then_updates_offer(
    client_for: ClientFor, world: World, products: dict[str, Product], session: AsyncSession
) -> None:
    client = await client_for(world.users["s.SUPPLIER_DESK"])
    created = await client.put("/supplier-offers", json=offer(products["SURG-KIT-A"]))
    assert created.status_code == 200
    body = created.json()
    assert body["org_id"] == str(world.supplier.id)
    assert (body["unit_price_paise"], body["lead_time_hours"], body["available_qty"]) == (
        1400,
        66,
        5000,
    )

    # Re-confirming unchanged values still bumps updated_at (freshness gate).
    same = await client.put(
        "/supplier-offers", json={**offer(products["SURG-KIT-A"]), "reason": "Stock checked"}
    )
    changed = await client.put("/supplier-offers", json=offer(products["SURG-KIT-A"], qty=4000))
    assert same.json()["id"] == changed.json()["id"] == body["id"]
    assert body["updated_at"] < same.json()["updated_at"] < changed.json()["updated_at"]
    assert changed.json()["available_qty"] == 4000

    rows = await audit_rows(session, body["id"])
    assert [(r.action, r.reason_source) for r in rows] == [
        ("supplier_offer.created", "SYSTEM"),
        ("supplier_offer.updated", "USER"),
        ("supplier_offer.updated", "SYSTEM"),
    ]
    assert rows[0].before is None and rows[0].reason == "No reason was entered."
    assert rows[2].before == {
        "unit_price_paise": 1400,
        "lead_time_hours": 66,
        "available_qty": 5000,
    }
    assert rows[2].after is not None and rows[2].after["available_qty"] == 4000


@pytest.mark.parametrize("user", ["a.STORE_MANAGER", "a.ADMIN", "s.DISPATCHER"])
async def test_only_supplier_org_users_with_po_respond_put_offers(
    client_for: ClientFor, world: World, products: dict[str, Product], user: str
) -> None:
    """Hospital ADMIN has po.respond but is not a SUPPLIER org; DISPATCHER lacks it."""
    client = await client_for(world.users[user])
    r = await client.put("/supplier-offers", json=offer(products["SURG-KIT-A"]))
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"


async def test_offers_are_visible_to_their_own_org_only(
    client_for: ClientFor, world: World, products: dict[str, Product], session: AsyncSession
) -> None:
    other = Organization(name="Supplier T", type=OrgType.SUPPLIER, lat=13.0, lng=77.6)
    session.add(other)
    await session.flush()
    desk = await session.scalar(select(Role).where(Role.name == "SUPPLIER_DESK"))
    other_user = User(
        email="desk@t.test",
        password_hash=password_hash(),
        full_name="Desk T",
        org_id=other.id,
        org=other,
        roles=[desk],
    )
    session.add(other_user)
    await session.flush()

    own = await client_for(world.users["s.SUPPLIER_DESK"])
    await own.put("/supplier-offers", json=offer(products["SURG-KIT-A"]))
    assert len((await own.get("/supplier-offers")).json()["items"]) == 1
    for user in (other_user, world.users["a.STORE_MANAGER"]):
        r = await (await client_for(user)).get("/supplier-offers")
        assert (r.status_code, r.json()["items"]) == (200, [])


async def test_offer_validation(
    client_for: ClientFor, world: World, products: dict[str, Product]
) -> None:
    client = await client_for(world.users["s.SUPPLIER_DESK"])
    unknown = {**offer(products["SURG-KIT-A"]), "product_id": str(uuid.uuid4())}
    assert (await client.put("/supplier-offers", json=unknown)).status_code == 400
    negative = offer(products["SURG-KIT-A"], price=-1)
    assert (await client.put("/supplier-offers", json=negative)).status_code == 422
    extra = {**offer(products["SURG-KIT-A"]), "org_id": str(world.hospital_a.id)}
    assert (await client.put("/supplier-offers", json=extra)).status_code == 422


async def test_offer_values_must_fit_int4_and_reason_must_be_nul_free(
    client_for: ClientFor, world: World, products: dict[str, Product]
) -> None:
    """int4 columns and Postgres text: a 422, never a 500."""
    client = await client_for(world.users["s.SUPPLIER_DESK"])
    kit = products["SURG-KIT-A"]
    too_big = 2_147_483_648
    for body in [
        offer(kit, qty=too_big),
        offer(kit, price=too_big),
        offer(kit, lead=too_big),
        {**offer(kit), "reason": "checked\u0000"},
    ]:
        r = await client.put("/supplier-offers", json=body)
        assert (r.status_code, r.json()["code"]) == (422, "schema_error"), body


class FrozenClock(datetime):
    """An app clock stuck in 2000: offer timestamps must come from the database clock."""

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> Self:
        return cls(2000, 1, 1, tzinfo=UTC)


async def test_offer_updated_at_comes_from_the_database_clock(
    client_for: ClientFor,
    world: World,
    products: dict[str, Product],
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(catalog_service, "datetime", FrozenClock, raising=False)
    client = await client_for(world.users["s.SUPPLIER_DESK"])
    await client.put("/supplier-offers", json=offer(products["SURG-KIT-A"]))
    before = await session.scalar(select(func.clock_timestamp()))
    updated = await client.put("/supplier-offers", json=offer(products["SURG-KIT-A"], qty=1))
    after = await session.scalar(select(func.clock_timestamp()))
    assert updated.status_code == 200 and before is not None and after is not None
    assert before <= datetime.fromisoformat(updated.json()["updated_at"]) <= after
