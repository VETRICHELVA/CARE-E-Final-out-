from fastapi import FastAPI

from app import errors, log
from app.audit.router import router as audit_router
from app.auth.router import router as auth_router
from app.catalog.router import router as catalog_router
from app.events.router import router as events_router
from app.inventory.router import router as inventory_router
from app.iot.router import router as iot_router
from app.network.router import router as network_router
from app.orgs.router import router as orgs_router
from app.purchase_orders.router import router as purchase_orders_router
from app.recommendations.router import router as recommendations_router
from app.shortages.router import router as shortages_router
from app.source_requests.router import router as source_requests_router

API = "/api/v1"


def create_app() -> FastAPI:
    app = FastAPI(
        title="CARE-E hub API",
        version="0.1.0",
        openapi_url=f"{API}/openapi.json",
        docs_url=f"{API}/docs",
        redoc_url=None,
    )
    errors.install(app)
    log.install(app)
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
    ):
        app.include_router(router, prefix=API)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
