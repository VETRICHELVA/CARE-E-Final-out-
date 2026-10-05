import runpy
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.auth.models import User
from app.catalog.models import Product, SupplierOffer
from app.catalog.schemas import OfferIn
from app.db import flush_or_conflict
from app.errors import AppError

CATALOG_FILE = Path(__file__).resolve().parents[4] / "scripts" / "seed" / "catalog.py"
OFFER_FIELDS = ("unit_price_paise", "lead_time_hours", "available_qty")


async def seed_catalog(session: AsyncSession) -> dict[str, Product]:
    """Insert or update every product in scripts/seed/catalog.py by code; idempotent."""
    data = runpy.run_path(str(CATALOG_FILE))
    t_min, t_max = data["COLD_CHAIN_C"]
    existing = {p.code: p for p in await session.scalars(select(Product))}
    for code, name, category, unit, cold, shelf_life in data["PRODUCTS"]:
        product = existing.get(code) or Product(code=code)
        product.name, product.category, product.unit = name, category, unit
        product.requires_cold_chain = cold
        product.temp_min_c, product.temp_max_c = (t_min, t_max) if cold else (None, None)
        product.default_min_shelf_life_days = shelf_life
        session.add(product)
        existing[code] = product
    await session.flush()
    return existing


async def get_product(session: AsyncSession, product_id: object) -> Product:
    product = await session.get(Product, product_id)
    if product is None:
        raise AppError(404, "not_found", "Product not found.")
    return product


async def put_offer(session: AsyncSession, user: User, body: OfferIn) -> SupplierOffer:
    """Create or update the caller's offer for one product. Always bumps `updated_at`:
    a re-confirmed offer is fresh for the freshness gate even if nothing changed."""
    if await session.get(Product, body.product_id) is None:
        raise AppError(400, "validation", "Unknown product.", {"product_id": str(body.product_id)})
    values = body.model_dump(include=set(OFFER_FIELDS))
    offer = await session.scalar(
        select(SupplierOffer).where(
            SupplierOffer.org_id == user.org_id, SupplierOffer.product_id == body.product_id
        )
    )
    if offer is None:
        offer = SupplierOffer(org_id=user.org_id, product_id=body.product_id, **values)
        await flush_or_conflict(session, offer, "This offer was just created; try again.")
        action, before = "supplier_offer.created", None
    else:
        before = {f: getattr(offer, f) for f in OFFER_FIELDS}
        for field, value in values.items():
            setattr(offer, field, value)
        offer.updated_at = func.clock_timestamp()  # the DB clock, like every other timestamp
        action = "supplier_offer.updated"
    await session.flush()
    await session.refresh(offer)
    after = {"product_id": offer.product_id, **{f: getattr(offer, f) for f in OFFER_FIELDS}}
    await audit.record(
        session, user, "supplier_offer", offer.id, action, before, after, body.reason
    )
    return offer
