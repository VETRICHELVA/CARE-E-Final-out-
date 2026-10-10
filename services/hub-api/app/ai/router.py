"""GET /ai/read/*: the copilot's read-only tools (S13; apps-ai-iot.md, Copilot).

Callable only with the AI service token plus `X-On-Behalf-Of: <the user's access token>`
(`app.ai.guard`). Each endpoint answers exactly as the user's own endpoint would answer that
user: same org scope (403 for another org's record), same capability checks, same schemas and
redaction. There are no write endpoints here, and the AI token opens nothing else."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.ai import service
from app.ai.guard import AiUser
from app.ai.schemas import AiColdChainOut, AiShortageOut, ProductMatchOut, ProductSearchOut
from app.audit.models import AuditLog
from app.audit.schemas import AuditOut
from app.auth.capabilities import Capability
from app.auth.deps import ensure_any, is_platform_admin, org_scoped
from app.catalog import service as catalog
from app.catalog.models import Product
from app.db import NulFreeStr, SessionDep
from app.domain import product_search
from app.pagination import Cursor, Limit, Page, paginate
from app.recommendations import router as recommendations
from app.recommendations.schemas import RecommendationOut
from app.shipments import router as shipments
from app.shipments.schemas import ShipmentDetailOut
from app.shortages import router as shortages
from app.shortages.schemas import CandidateOut, MatchRunOut

router = APIRouter(prefix="/ai/read", tags=["ai"])

C = Capability


@router.get("/shortages/{shortage_id}")
async def ai_shortage(shortage_id: uuid.UUID, user: AiUser, session: SessionDep) -> AiShortageOut:
    """get_shortage: GET /shortages/{id} (needs `shortage.create` or `recommendation.approve`)
    with the product, facility, its source requests (requester's view), recommendations,
    shipments and residual shortages."""
    ensure_any(user, C.SHORTAGE_CREATE, C.RECOMMENDATION_APPROVE)
    return await service.shortage_view(session, user, shortage_id)


@router.get("/shortages/{shortage_id}/match-run")
async def ai_match_run(shortage_id: uuid.UUID, user: AiUser, session: SessionDep) -> MatchRunOut:
    """get_match_run: GET /shortages/{id}/match-runs/latest."""
    return await shortages.latest_match_run(shortage_id, user, session)


@router.get("/candidates/{candidate_id}")
async def ai_candidate(candidate_id: uuid.UUID, user: AiUser, session: SessionDep) -> CandidateOut:
    """get_candidate: one candidate as the match run shows it (no hospital cost)."""
    return await service.candidate_view(session, user, candidate_id)


@router.get("/recommendations/{recommendation_id}")
async def ai_recommendation(
    recommendation_id: uuid.UUID, user: AiUser, session: SessionDep
) -> RecommendationOut:
    """get_recommendation: GET /recommendations/{id} (supplier costs only)."""
    return await recommendations.get_recommendation(recommendation_id, user, session)


@router.get("/shipments/{shipment_id}")
async def ai_shipment(
    shipment_id: uuid.UUID, user: AiUser, session: SessionDep
) -> ShipmentDetailOut:
    """get_shipment: GET /shipments/{id} (the receipt for the receiving org only), without
    the route geometry, which only a map needs."""
    detail = await shipments.get_shipment(shipment_id, user, session)
    return detail.model_copy(update={"route_geometry": None})


@router.get("/shipments/{shipment_id}/coldchain")
async def ai_coldchain(shipment_id: uuid.UUID, user: AiUser, session: SessionDep) -> AiColdChainOut:
    """get_coldchain_events: whether the shipment needs cold chain, its readings summary and
    excursion events (none until S15). Involved orgs only, as GET /shipments/{id}."""
    return await service.coldchain_view(session, user, shipment_id)


@router.get("/audit")
async def ai_audit(
    user: AiUser,
    session: SessionDep,
    entity: Annotated[NulFreeStr, Query(description="e.g. shortage, source_request, shipment")],
    entity_id: uuid.UUID,
    limit: Limit = 100,
    cursor: Cursor = None,
) -> Page[AuditOut]:
    """get_audit: needs `audit.read`. `entity=shortage` is that shortage's whole trail as
    GET /shortages/{id}/audit; any other entity is GET /audit?entity=&entity_id= (own org's
    rows). Oldest first."""
    ensure_any(user, C.AUDIT_READ)
    if entity == "shortage":
        return await shortages.shortage_audit(entity_id, user, session, limit, cursor)
    stmt = select(AuditLog).where(AuditLog.entity == entity, AuditLog.entity_id == entity_id)
    if not is_platform_admin(user):
        stmt = org_scoped(stmt, user)
    rows, next_cursor = await paginate(session, stmt, AuditLog.ts, AuditLog.id, limit, cursor)
    return Page[AuditOut](items=[AuditOut.model_validate(r) for r in rows], next_cursor=next_cursor)


@router.get("/products/search")
async def ai_product_search(
    user: AiUser,
    session: SessionDep,
    q: Annotated[
        NulFreeStr, Query(min_length=1, max_length=200, description="The words the user typed.")
    ],
    limit: Annotated[int, Query(ge=1, le=20)] = product_search.DEFAULT_LIMIT,
) -> ProductSearchOut:
    """Chat ordering (S17): fuzzy match on product name, code and synonyms
    (scripts/seed/synonyms.yaml), best first, each with its score; products under the
    minimum score are left out. The catalog is the same for every user (GET /products), so
    this reads nothing of any org. It ranks and never picks."""
    items = []
    for m in product_search.search(q, await catalog.search_entries(session), limit=limit):
        p = m.key
        assert isinstance(p, Product)
        items.append(
            ProductMatchOut(
                product_id=p.id,
                code=p.code,
                name=p.name,
                category=p.category,
                unit=p.unit,
                requires_cold_chain=p.requires_cold_chain,
                default_min_shelf_life_days=p.default_min_shelf_life_days,
                score=m.score,
                matched_on=m.matched_on,
            )
        )
    return ProductSearchOut(q=q, items=items)
