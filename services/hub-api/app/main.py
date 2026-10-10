from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import errors, log
from app.ai.guard import AiTokenGuard
from app.ai.router import router as ai_router
from app.audit.router import router as audit_router
from app.auth.router import router as auth_router
from app.catalog.router import router as catalog_router
from app.coldchain.router import router as coldchain_router
from app.config import settings
from app.db import engine
from app.dbrole import require_least_privilege
from app.events.router import router as events_router
from app.forecasting.router import router as forecasting_router
from app.inventory.router import router as inventory_router
from app.iot.router import router as iot_router
from app.metrics.router import router as metrics_router
from app.network.router import router as network_router
from app.notifications.router import router as notifications_router
from app.orgs.router import router as orgs_router
from app.purchase_orders.router import router as purchase_orders_router
from app.receiving.router import router as receiving_router
from app.recommendations.router import router as recommendations_router
from app.routing.router import router as routing_router
from app.shipments.router import router as shipments_router
from app.shortages.router import router as shortages_router
from app.source_requests.router import router as source_requests_router
from app.surplus.router import router as surplus_router
from app.trust.router import router as trust_router

API = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # S20: outside dev, refuse a database role that could rewrite the audit log.
    await require_least_privilege(engine)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="CARE-E hub API",
        version="0.1.0",
        openapi_url=f"{API}/openapi.json",
        docs_url=f"{API}/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    errors.install(app)
    log.install(app)
    app.add_middleware(AiTokenGuard)  # the AI token opens GET /ai/read/* only (S13)
    # S20: browsers may call the hub only from the three apps' origins (CORS_ORIGINS). Bearer
    # tokens, no cookies, so no credentials mode. Added last: it answers preflights first.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "If-Match",
            "Last-Event-ID",
            "X-Request-ID",
        ],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )
    for router in (
        auth_router,
        orgs_router,
        audit_router,
        catalog_router,
        inventory_router,
        shortages_router,
        iot_router,
        source_requests_router,
        events_router,
        recommendations_router,
        purchase_orders_router,
        network_router,
        shipments_router,
        routing_router,
        receiving_router,
        notifications_router,
        ai_router,
        coldchain_router,
        forecasting_router,
        surplus_router,
        trust_router,
        metrics_router,
    ):
        app.include_router(router, prefix=API)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
