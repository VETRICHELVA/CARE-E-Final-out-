"""Forecast runs and what they mean for a hospital's stock (apps-ai-iot.md, Forecasting).

A run fits each hospital x product series of ConsumptionRecord (`app.forecasting.model`,
statistics in the hub worker, never an LLM) and replaces that series' Forecast rows. The
stock-out date, reorder suggestion and expiry-risk excess are derived when read, from the
stored forecast and the stock recorded now (`app.domain.forecast`), so a stock change shows
at once without a new run."""

import asyncio
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import SupplierOffer
from app.domain import config
from app.domain import forecast as rules
from app.domain.forecast import Day
from app.domain.inventory import batch_transferable
from app.forecasting import model
from app.forecasting.models import ConsumptionRecord, Forecast
from app.inventory.models import InventoryBatch
from app.orgs.models import Organization, OrgType
from app.source_requests.holds import held_by_batch


def today() -> date:
    """Forecast dates are UTC dates, like expiry (app.inventory.service.today)."""
    return datetime.now(UTC).date()


@dataclass
class RunSummary:
    org_ids: list[uuid.UUID] = field(default_factory=list)
    series: int = 0
    models: dict[str, int] = field(default_factory=dict)


async def hospital_org_ids(session: AsyncSession) -> list[uuid.UUID]:
    stmt = select(Organization.id).where(Organization.type == OrgType.HOSPITAL)
    return sorted(await session.scalars(stmt), key=str)


async def run_org(session: AsyncSession, org_id: uuid.UUID, day: date) -> RunSummary:
    """Forecast every product the org has consumption history for (days before `day`) and
    replace its Forecast rows. The caller commits."""
    summary = RunSummary(org_ids=[org_id])
    stmt = (
        select(ConsumptionRecord.product_id)
        .where(ConsumptionRecord.org_id == org_id, ConsumptionRecord.date < day)
        .distinct()
    )
    for product_id in sorted(await session.scalars(stmt), key=str):
        version = await run_series(session, org_id, product_id, day)
        summary.series += 1
        summary.models[version] = summary.models.get(version, 0) + 1
    return summary


async def run(session: AsyncSession, org_ids: Iterable[uuid.UUID], day: date) -> RunSummary:
    """Forecast every org in `org_ids`, committing after each org."""
    total = RunSummary()
    for org_id in org_ids:
        one = await run_org(session, org_id, day)
        await session.commit()
        total.org_ids.append(org_id)
        total.series += one.series
        for version, n in one.models.items():
            total.models[version] = total.models.get(version, 0) + n
    return total


async def run_series(
    session: AsyncSession, org_id: uuid.UUID, product_id: uuid.UUID, day: date
) -> str:
    """Fit one series and replace its Forecast rows; returns the model version used."""
    records = list(
        await session.execute(
            select(ConsumptionRecord.date, ConsumptionRecord.qty)
            .where(
                ConsumptionRecord.org_id == org_id,
                ConsumptionRecord.product_id == product_id,
                ConsumptionRecord.date < day,
            )
            .order_by(ConsumptionRecord.date)
        )
    )
    first = records[0][0]
    history = [0.0] * (day - first).days  # a day with no record used nothing
    for d, qty in records:
        history[(d - first).days] = float(qty)
    expiries = await session.scalars(
        select(InventoryBatch.expiry_date).where(
            InventoryBatch.org_id == org_id, InventoryBatch.product_id == product_id
        )
    )
    end = rules.horizon_end(day, expiries)
    result = await asyncio.to_thread(model.forecast, history, first, day, end)
    await session.execute(
        delete(Forecast).where(Forecast.org_id == org_id, Forecast.product_id == product_id)
    )
    session.add_all(
        Forecast(
            org_id=org_id,
            product_id=product_id,
            date=d.date,
            predicted_qty=d.predicted,
            lower=d.lower,
            upper=d.upper,
            model_version=result.model_version,
        )
        for d in result.days
    )
    await session.flush()
    return result.model_version


# --- reading a stored forecast --------------------------------------------------------------


@dataclass(frozen=True)
class BatchRisk:
    batch: InventoryBatch
    transferable: int
    usage_before_expiry: float
    excess: int

    @property
    def suggested_qty(self) -> int:
        """What a surplus post could offer: the excess, never more than transferable."""
        return min(self.excess, self.transferable)


@dataclass(frozen=True)
class Outlook:
    """One product's stored forecast against the org's stock now."""

    org_id: uuid.UUID
    product_id: uuid.UUID
    model_version: str
    generated_at: datetime
    synthetic_history: bool
    days: list[Day]  # from today on, the whole stored horizon
    usable_stock: int
    safety_stock: int
    stockout_date: date | None
    lead_time_days: int
    reorder_qty: int | None
    expiry_risks: list[BatchRisk]


async def stored_days(
    session: AsyncSession, org_id: uuid.UUID, product_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[Forecast]]:
    rows = await session.scalars(
        select(Forecast)
        .where(Forecast.org_id == org_id, Forecast.product_id.in_(product_ids))
        .order_by(Forecast.product_id, Forecast.date)
    )
    out: dict[uuid.UUID, list[Forecast]] = defaultdict(list)
    for row in rows:
        out[row.product_id].append(row)
    return out


async def outlooks(
    session: AsyncSession, org_id: uuid.UUID, product_ids: Sequence[uuid.UUID], day: date
) -> list[Outlook]:
    """The outlook of each product in `product_ids` that has a stored forecast."""
    forecasts = await stored_days(session, org_id, product_ids)
    ids = [p for p in product_ids if forecasts.get(p)]
    if not ids:
        return []
    batches: dict[uuid.UUID, list[InventoryBatch]] = defaultdict(list)
    for b in await session.scalars(
        select(InventoryBatch)
        .where(InventoryBatch.org_id == org_id, InventoryBatch.product_id.in_(ids))
        .order_by(InventoryBatch.expiry_date, InventoryBatch.id)
    ):
        batches[b.product_id].append(b)
    held = await held_by_batch(session, (b.id for bs in batches.values() for b in bs))
    synthetic = set(
        await session.scalars(
            select(ConsumptionRecord.product_id)
            .where(
                ConsumptionRecord.org_id == org_id,
                ConsumptionRecord.product_id.in_(ids),
                ConsumptionRecord.synthetic.is_(True),
            )
            .distinct()
        )
    )
    lead_hours: dict[uuid.UUID, list[int]] = defaultdict(list)
    for product_id, hours in await session.execute(
        select(SupplierOffer.product_id, SupplierOffer.lead_time_hours).where(
            SupplierOffer.product_id.in_(ids)
        )
    ):
        lead_hours[product_id].append(hours)
    out = []
    for product_id in ids:
        rows = forecasts[product_id]
        days = rules.from_today((Day(r.date, r.predicted_qty, r.lower, r.upper) for r in rows), day)
        stock = batches.get(product_id, [])
        usable = rules.usable_stock(((b, held.get(b.id, 0)) for b in stock), day)
        safety = sum(b.safety_stock for b in stock if b.expiry_date > day)
        lead = rules.lead_time_days(lead_hours.get(product_id, []))
        risks = []
        for b in stock:
            risk = rules.expiry_risk(days, day, b.expiry_date, b.on_hand, b.safety_stock)
            if risk is not None:
                transferable = batch_transferable(b, day, held.get(b.id, 0))
                risks.append(BatchRisk(b, transferable, risk.usage_before_expiry, risk.excess))
        out.append(
            Outlook(
                org_id=org_id,
                product_id=product_id,
                model_version=rows[0].model_version,
                generated_at=min(r.created_at for r in rows),
                synthetic_history=product_id in synthetic,
                days=days,
                usable_stock=usable,
                safety_stock=safety,
                stockout_date=rules.stockout_date(days, usable, day),
                lead_time_days=lead,
                reorder_qty=rules.reorder_qty(days, day, lead, safety, usable),
                expiry_risks=risks,
            )
        )
    return out


async def forecast_product_ids(
    session: AsyncSession, org_id: uuid.UUID, product_id: uuid.UUID | None = None
) -> list[uuid.UUID]:
    stmt = select(Forecast.product_id).where(Forecast.org_id == org_id).distinct()
    if product_id is not None:
        stmt = stmt.where(Forecast.product_id == product_id)
    return sorted(await session.scalars(stmt), key=str)


async def stockouts_within(
    session: AsyncSession, product_id: uuid.UUID, day: date, exclude_org_id: uuid.UUID
) -> dict[uuid.UUID, date]:
    """Every other org whose stored forecast for the product runs out of usable stock
    within SURPLUS_STOCKOUT_MATCH_DAYS of `day`: org -> predicted stock-out date."""
    stmt = (
        select(Forecast.org_id)
        .where(
            Forecast.product_id == product_id,
            Forecast.org_id != exclude_org_id,
            Forecast.date >= day,
        )
        .distinct()
    )
    limit = day + timedelta(days=config.SURPLUS_STOCKOUT_MATCH_DAYS)
    out = {}
    for org_id in sorted(await session.scalars(stmt), key=str):
        (outlook,) = await outlooks(session, org_id, [product_id], day)
        if outlook.stockout_date is not None and outlook.stockout_date <= limit:
            out[org_id] = outlook.stockout_date
    return out
