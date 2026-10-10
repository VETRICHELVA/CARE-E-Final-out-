import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.auth.capabilities import Capability
from app.auth.deps import CurrentUser, is_platform_admin, require
from app.auth.models import User
from app.catalog.models import Product
from app.db import SessionDep
from app.domain import config
from app.domain.surplus import LIVE
from app.errors import AppError
from app.forecasting import service
from app.forecasting.models import Forecast
from app.forecasting.schemas import (
    ExpiryRiskOut,
    ForecastDayOut,
    ForecastOut,
    ReorderOut,
    RunOut,
)
from app.orgs.models import Organization, OrgType
from app.pagination import Cursor, Limit, Page, paginate
from app.surplus.models import SurplusPost

router = APIRouter(prefix="/forecasts", tags=["forecasts"])

Runner = Annotated[User, Depends(require(Capability.INVENTORY_EDIT))]


@router.get("")
async def list_forecasts(
    user: CurrentUser,
    session: SessionDep,
    product_id: uuid.UUID | None = None,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> Page[ForecastOut]:
    """The caller's own org's forecasts, one per product with a stored forecast (catalog
    order): the next 30 days with their interval, the predicted stock-out date, the reorder
    suggestion and each expiry-risk batch, against the stock recorded now."""
    has_forecast = select(Forecast.product_id).where(Forecast.org_id == user.org_id)
    if product_id is not None:
        has_forecast = has_forecast.where(Forecast.product_id == product_id)
    stmt = select(Product).where(Product.id.in_(has_forecast))
    products, next_cursor = await paginate(
        session, stmt, Product.created_at, Product.id, limit, cursor
    )
    day = service.today()
    outlooks = await service.outlooks(session, user.org_id, [p.id for p in products], day)
    batch_ids = [r.batch.id for o in outlooks for r in o.expiry_risks]
    posts = {
        batch_id: post_id
        for batch_id, post_id in await session.execute(
            select(SurplusPost.batch_id, SurplusPost.id).where(
                SurplusPost.batch_id.in_(batch_ids), SurplusPost.status.in_(LIVE)
            )
        )
    }
    end = day + timedelta(days=config.FORECAST_HORIZON_DAYS)
    items = [
        ForecastOut(
            product_id=o.product_id,
            model_version=o.model_version,
            generated_at=o.generated_at,
            synthetic_history=o.synthetic_history,
            days=[
                ForecastDayOut(date=d.date, predicted_qty=d.predicted, lower=d.lower, upper=d.upper)
                for d in o.days
                if d.date < end
            ],
            usable_stock=o.usable_stock,
            safety_stock=o.safety_stock,
            stockout_date=o.stockout_date,
            reorder=None
            if o.reorder_qty is None
            else ReorderOut(lead_time_days=o.lead_time_days, qty=o.reorder_qty),
            expiry_risks=[
                ExpiryRiskOut(
                    batch_id=r.batch.id,
                    batch_no=r.batch.batch_no,
                    expiry_date=r.batch.expiry_date,
                    on_hand=r.batch.on_hand,
                    safety_stock=r.batch.safety_stock,
                    transferable=r.transferable,
                    usage_before_expiry=r.usage_before_expiry,
                    excess=r.excess,
                    suggested_qty=r.suggested_qty,
                    surplus_post_id=posts.get(r.batch.id),
                )
                for r in o.expiry_risks
            ],
        )
        for o in outlooks
    ]
    return Page[ForecastOut](items=items, next_cursor=next_cursor)


@router.post("/run")
async def run_forecasts(
    user: Runner, session: SessionDep, org_id: uuid.UUID | None = None
) -> RunOut:
    """Forecast now instead of waiting for the nightly job. A platform admin runs every
    hospital (or `org_id`); any other caller needs `inventory.edit` in a HOSPITAL org and runs
    its own org only (403 for another `org_id`). Statistics, not an LLM: the same history
    gives the same numbers."""
    if is_platform_admin(user):
        if org_id is None:
            org_ids = await service.hospital_org_ids(session)
        else:
            org = await session.get(Organization, org_id)
            if org is None or org.type != OrgType.HOSPITAL:
                raise AppError(400, "validation", "Not a hospital.", {"org_id": str(org_id)})
            org_ids = [org_id]
    else:
        if user.org.type != OrgType.HOSPITAL:
            raise AppError(403, "forbidden", "Only hospitals have forecasts.")
        if org_id is not None and org_id != user.org_id:
            raise AppError(403, "forbidden", "You can only run your own organization's forecast.")
        org_ids = [user.org_id]
    summary = await service.run(session, org_ids, service.today())
    return RunOut(org_ids=summary.org_ids, series=summary.series, models=summary.models)
