"""Recommendations (business-rules.md §5, §6, §7 steps 4-6, §8, §10, §13).

Created once every planned source holds stock, or straight after a BUY run; the requester's
approver then approves (holds FIRM, requests CONFIRMED and one shipment per source, or a
SENT purchase order), rejects (release and re-run) or escalates (every APPROVER of the org
is notified). Past its validity the timer expires it (release and re-run).

Lock order, as in `app.source_requests.service`: the shortage, then the recommendation, then
source requests, then holds."""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.auth.capabilities import RoleName
from app.auth.models import User
from app.catalog.models import SupplierOffer
from app.domain import recommendation as rules
from app.domain.events import EventType
from app.domain.recommendation import OPEN_RECOMMENDATION, RecStatus
from app.domain.resolution import BUY
from app.domain.shortage import Status
from app.domain.source_request import HoldStatus, RequestStatus
from app.domain.state_machine import InvalidTransition
from app.errors import AppError
from app.events import service as events
from app.inventory.models import InventoryBatch
from app.notifications import service as notifications
from app.orgs.models import Organization
from app.purchase_orders import service as purchase_orders
from app.purchase_orders.models import PurchaseOrder
from app.recommendations import transitions
from app.recommendations.models import Recommendation
from app.shipments import service as shipments
from app.shortages import service as shortages
from app.shortages.models import Candidate, MatchRun, Priority, Shortage, SourceType, Trigger
from app.source_requests import holds
from app.source_requests import service as source_requests
from app.source_requests.models import Hold, SourceRequest
from app.source_requests.service import Settled

log = logging.getLogger("app.recommendations")

ENTITY = transitions.ENTITY
ESCALATED_NOTIFICATION = "recommendation.escalated"
CREATED = "Recommendation created."


# --- creating --------------------------------------------------------------------------------


async def _names(session: AsyncSession, org_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    stmt = select(Organization.id, Organization.name).where(Organization.id.in_(org_ids))
    return {org_id: name for org_id, name in await session.execute(stmt)}


async def _shelf_life_days(session: AsyncSession, sr: SourceRequest, arrival: datetime) -> int:
    """Days of shelf life left at delivery on the stock held for `sr`: its earliest-expiring
    held batch (§2 days_to_expiry_at_delivery)."""
    expiry = await session.scalar(
        select(InventoryBatch.expiry_date)
        .join(Hold, Hold.batch_id == InventoryBatch.id)
        .where(Hold.source_request_id == sr.id, Hold.status == HoldStatus.TENTATIVE)
        .order_by(InventoryBatch.expiry_date)
        .limit(1)
    )
    if expiry is None:
        raise ValueError(f"Source request {sr.id} holds no stock.")
    return (expiry - arrival.date()).days


async def _line(
    session: AsyncSession,
    shortage: Shortage,
    planned: dict[str, Any],
    names: dict[uuid.UUID, str],
    now: datetime,
) -> dict[str, Any]:
    """A stored recommendation line: the planned line plus what the explanation states.
    Hospital lines keep their landed cost here (as the match run's plan does); it is dropped
    from every response to the requester."""
    org_id = uuid.UUID(planned["source_org_id"])
    line: dict[str, Any] = {
        "candidate_id": planned["candidate_id"],
        "source_org_id": planned["source_org_id"],
        "source_org_name": names[org_id],
        "source_type": planned["source_type"],
        "qty": planned["qty"],
        "eta_hours": planned["eta_hours"],
        "landed_cost_paise": planned["landed_cost_paise"],
        "unit_price_paise": None,
        "shelf_life_days": None,
        "source_request_id": None,
    }
    if planned["source_type"] == SourceType.HOSPITAL:
        sr = await session.scalar(
            select(SourceRequest).where(
                SourceRequest.candidate_id == uuid.UUID(planned["candidate_id"]),
                SourceRequest.status == RequestStatus.TENTATIVE_HOLD,
            )
        )
        if sr is None:
            raise ValueError(f"Candidate {planned['candidate_id']} holds no stock.")
        arrival = now + timedelta(hours=planned["eta_hours"])
        line["source_request_id"] = str(sr.id)
        line["qty"] = sr.qty
        line["shelf_life_days"] = await _shelf_life_days(session, sr, arrival)
    else:
        line["unit_price_paise"] = await session.scalar(
            select(SupplierOffer.unit_price_paise).where(
                SupplierOffer.org_id == org_id, SupplierOffer.product_id == shortage.product_id
            )
        )
    return line


def _source(line: dict[str, Any]) -> rules.Source:
    """What the explanation may say about a line: never a hospital's cost (rule 6)."""
    supplier = line["source_type"] == SourceType.SUPPLIER
    return rules.Source(
        name=line["source_org_name"],
        qty=line["qty"],
        eta_hours=line["eta_hours"],
        shelf_life_days=line["shelf_life_days"],
        cost_paise=line["landed_cost_paise"] if supplier else None,
    )


async def _left_out(
    session: AsyncSession, shortage: Shortage, run: MatchRun, names: dict[uuid.UUID, str]
) -> list[tuple[str, str]]:
    """Why each org excluded from this shortage's matching was left out (§7 step 6). A
    residual inherits its parent's exclusions (§9), so the reason may come from an earlier
    shortage it continues."""
    lineage = await _lineage(session, shortage)
    out = []
    for org_id in run.excluded_org_ids:
        why = None
        for shortage_id in lineage:
            why = await _why_excluded(session, shortage_id, org_id)
            if why is not None:
                if shortage_id != shortage.id:
                    why += " for the earlier shortage"
                break
        out.append((names.get(org_id, str(org_id)), why or "left out after an earlier request"))
    return out


async def _lineage(session: AsyncSession, shortage: Shortage) -> list[uuid.UUID]:
    """The shortage, then its parent, grandparent and so on (residuals, §9)."""
    ids = [shortage.id]
    parent = shortage.parent_shortage_id
    while parent is not None and parent not in ids:
        ids.append(parent)
        parent = await session.scalar(
            select(Shortage.parent_shortage_id).where(Shortage.id == parent)
        )
    return ids


async def _why_excluded(
    session: AsyncSession, shortage_id: uuid.UUID, org_id: uuid.UUID
) -> str | None:
    rejected_po = await session.scalar(
        select(PurchaseOrder.id).where(
            PurchaseOrder.shortage_id == shortage_id,
            PurchaseOrder.supplier_org_id == org_id,
            PurchaseOrder.status == "REJECTED",
        )
    )
    if rejected_po:
        return "rejected the purchase order"
    statuses = set(
        await session.scalars(
            select(SourceRequest.status).where(
                SourceRequest.shortage_id == shortage_id,
                SourceRequest.source_org_id == org_id,
                SourceRequest.responded_at.is_(None) | (SourceRequest.status == "DECLINED"),
            )
        )
    )
    if RequestStatus.DECLINED in statuses:
        return "declined the request"
    rejected = await session.scalars(
        select(Recommendation.lines).where(
            Recommendation.shortage_id == shortage_id,
            Recommendation.status == RecStatus.REJECTED,
        )
    )
    if any(str(line["source_org_id"]) == str(org_id) for lines in rejected for line in lines):
        return "was in a recommendation the requester rejected"
    if RequestStatus.EXPIRED in statuses:
        return "did not answer the request in time"
    return None


async def create(
    session: AsyncSession, shortage: Shortage, run: MatchRun, *, now: datetime | None = None
) -> Recommendation | None:
    """§7 step 4: the recommendation for `run`'s plan, then MATCHING -> AWAITING_DECISION
    and `recommendation.ready`. Does nothing unless `run` is the shortage's latest run, has
    a plan and no recommendation yet, and the shortage is MATCHING."""
    now = now or datetime.now(UTC)
    plan = run.planned_resolution
    if plan is None or shortage.status != Status.MATCHING:
        return None
    latest = await shortages.latest_run(session, shortage.id)
    if latest is None or latest.id != run.id:
        return None
    if await session.scalar(select(Recommendation.id).where(Recommendation.match_run_id == run.id)):
        return None

    candidates = list(
        await session.scalars(
            select(Candidate).where(Candidate.match_run_id == run.id).order_by(Candidate.id)
        )
    )
    names = await _names(session, {c.source_org_id for c in candidates} | set(run.excluded_org_ids))
    planned, asked = plan["lines"], plan.get("parallel") or []
    if asked:
        # CRITICAL parallel requests (S19): the line is the source that accepted first.
        holding = {
            str(c)
            for c in await session.scalars(
                select(SourceRequest.candidate_id).where(
                    SourceRequest.shortage_id == shortage.id,
                    SourceRequest.status == RequestStatus.TENTATIVE_HOLD,
                )
            )
        }
        planned = [x for x in asked if str(x["candidate_id"]) in holding]
    lines = [await _line(session, shortage, x, names, now) for x in planned]
    alternatives = [await _line(session, shortage, x, names, now) for x in plan["alternatives"]]
    used = {str(x["candidate_id"]) for x in (*lines, *alternatives, *asked)}
    rejected = sorted(
        (
            rules.Rejected(
                names[c.source_org_id],
                tuple(g["reason"] for g in c.gate_results if not g["passed"]),
            )
            for c in candidates
            if not c.eligible
        ),
        key=lambda r: r.name,
    )
    explanation = rules.explain(
        plan["type"],
        [_source(x) for x in lines],
        _source(alternatives[0]) if alternatives else None,
        shortfall=shortage.shortfall,
        critical=shortage.priority == Priority.CRITICAL,
        other_eligible=sum(c.eligible and str(c.id) not in used for c in candidates),
        rejected=rejected,
        left_out=await _left_out(session, shortage, run, names),
        asked_at_once=len(asked),
    )
    rec = Recommendation(
        id=uuid.uuid4(),
        shortage_id=shortage.id,
        match_run_id=run.id,
        type=plan["type"],
        lines=lines,
        alternatives=alternatives,
        explanation=explanation,
        status=RecStatus.PENDING,
        expires_at=rules.valid_until(shortage.priority, now),
    )
    session.add(rec)
    await session.flush()
    why = (
        f"Match run {run.run_no} planned a BUY."
        if rec.type == BUY
        else f"Every source planned by match run {run.run_no} holds stock."
    )
    # No line figures in the audit row: the requester's org reads it (rule 6).
    after = {
        "status": rec.status,
        "type": rec.type,
        "match_run_id": run.id,
        "expires_at": rec.expires_at,
    }
    await audit.record(
        session, None, ENTITY, rec.id, f"{ENTITY}.created", None, after, why,
        org_id=shortage.org_id,
    )  # fmt: skip
    await shortages.move_shortage(session, shortage, Status.AWAITING_DECISION, None, CREATED)
    await events.emit(
        session,
        EventType.RECOMMENDATION_READY,
        [shortage.org_id],
        {"recommendation_id": rec.id, "shortage_id": shortage.id, "type": rec.type},
    )
    return rec


# --- reads -----------------------------------------------------------------------------------


async def get(session: AsyncSession, user: User, rec_id: uuid.UUID) -> Recommendation:
    """Any user of the requester's org; 403 for every other org."""
    rec = await session.get(Recommendation, rec_id)
    if rec is None:
        raise AppError(404, "not_found", "Recommendation not found.")
    org_id = await session.scalar(select(Shortage.org_id).where(Shortage.id == rec.shortage_id))
    if org_id != user.org_id:
        raise AppError(403, "forbidden", "This recommendation belongs to another organization.")
    return rec


async def _locked(session: AsyncSession, rec_id: uuid.UUID) -> tuple[Recommendation, Shortage]:
    rec = await session.get_one(Recommendation, rec_id)
    shortage = await session.get_one(
        Shortage, rec.shortage_id, with_for_update=True, populate_existing=True
    )
    rec = await session.get_one(
        Recommendation, rec_id, with_for_update=True, populate_existing=True
    )
    return rec, shortage


# --- expiry ----------------------------------------------------------------------------------


async def _end_requests(
    session: AsyncSession, shortage: Shortage, reason: str
) -> list[SourceRequest]:
    """§8: TENTATIVE_HOLD -> EXPIRED when the recommendation is rejected or expires. The
    holds are released by `release_and_rematch` right after."""
    ended = []
    for sr in await holds.open_requests(session, shortage.id, lock=True):
        if sr.status == RequestStatus.TENTATIVE_HOLD:
            await holds.move_request(session, shortage, sr, RequestStatus.EXPIRED, None, reason)
            ended.append(sr)
    return ended


async def _expire(
    session: AsyncSession, shortage: Shortage, rec: Recommendation, now: datetime
) -> None:
    """Validity passed (§6): EXPIRED, end the requests, release every hold and re-run."""
    reason = rules.VALIDITY_PASSED
    await transitions.move(
        session, shortage, rec, RecStatus.EXPIRED, None, reason,
        decided_at=now, reason=reason, reason_source="SYSTEM",
    )  # fmt: skip
    await _end_requests(session, shortage, reason)
    await source_requests.release_and_rematch(
        session, shortage, reason, trigger=Trigger.RECOMMENDATION_EXPIRED, now=now
    )


async def _settle_if_expired(
    session: AsyncSession, shortage: Shortage, rec: Recommendation, to: RecStatus, now: datetime
) -> None:
    """A decision that arrives after the validity expires the recommendation instead, even
    if the timer has not run yet; the router commits that and returns 409."""
    if rec.status in OPEN_RECOMMENDATION and rules.is_expired(rec.expires_at, now):
        await _expire(session, shortage, rec, now)
        raise Settled(
            AppError(
                409,
                "invalid_transition",
                "This recommendation has expired, so matching has re-run.",
                {"from": RecStatus.EXPIRED, "to": to},
            )
        )


async def expire_one(session: AsyncSession, rec_id: uuid.UUID, now: datetime) -> bool:
    """Expire one overdue recommendation under its locks. Re-checks after locking, so a
    second worker, or a run after a restart, finds nothing left and returns False."""
    rec, shortage = await _locked(session, rec_id)
    if rec.status not in OPEN_RECOMMENDATION or not rules.is_expired(rec.expires_at, now):
        return False
    await _expire(session, shortage, rec, now)
    return True


async def expire_overdue(session: AsyncSession, now: datetime | None = None) -> int:
    """The timer job (§6): expire every open recommendation past its validity, one
    transaction each; returns how many. Idempotent and safe with several workers."""
    now = now or datetime.now(UTC)
    ids = list(
        await session.scalars(
            select(Recommendation.id)
            .where(
                Recommendation.status.in_(OPEN_RECOMMENDATION),
                Recommendation.expires_at <= now,
            )
            .order_by(Recommendation.id)
        )
    )
    done = 0
    for rec_id in ids:
        try:
            done += await expire_one(session, rec_id, now)
            await session.commit()
        except Exception:
            await session.rollback()
            log.exception("could not expire recommendation %s", rec_id)
    return done


# --- decisions -------------------------------------------------------------------------------


async def _decidable(
    session: AsyncSession, user: User, rec_id: uuid.UUID, to: RecStatus, now: datetime
) -> tuple[Recommendation, Shortage]:
    """Only the requester's org decides (403 otherwise); a decided or expired recommendation
    is a 409 (an overdue one is expired first)."""
    await get(session, user, rec_id)
    rec, shortage = await _locked(session, rec_id)
    await _settle_if_expired(session, shortage, rec, to, now)
    if rec.status not in OPEN_RECOMMENDATION:
        raise InvalidTransition(rec.status, to)
    return rec, shortage


def _typed(reason: str | None) -> str | None:
    return (reason or "").strip() or None


def _decision(user: User, typed: str | None, now: datetime) -> dict[str, Any]:
    return {
        "decided_by": user.id,
        "decided_at": now,
        "reason": typed,
        "reason_source": "USER" if typed else "SYSTEM",
    }


async def _requests_to_confirm(
    session: AsyncSession, shortage: Shortage, rec: Recommendation, now: datetime
) -> list[tuple[dict[str, Any], SourceRequest, list[Hold]]]:
    """Lock every planned request and its tentative holds. A request that no longer holds
    stock is a 409; a hold past its deadline expires its request first (Settled, 409)."""
    out = []
    for line in sorted(rec.lines, key=lambda x: x["source_request_id"]):
        sr = await session.get_one(
            SourceRequest,
            uuid.UUID(line["source_request_id"]),
            with_for_update=True,
            populate_existing=True,
        )
        if sr.status != RequestStatus.TENTATIVE_HOLD:
            raise InvalidTransition(sr.status, RequestStatus.CONFIRMED)
        tentative = list(
            await session.scalars(
                select(Hold)
                .where(Hold.source_request_id == sr.id, Hold.status == HoldStatus.TENTATIVE)
                .order_by(Hold.batch_id, Hold.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        if any(h.expires_at <= now for h in tentative):
            await source_requests.expire_one(session, sr.id, now)
            raise Settled(
                AppError(
                    409,
                    "invalid_transition",
                    "A hold expired before approval, so matching has re-run.",
                    {"from": RecStatus.EXPIRED, "to": RecStatus.APPROVED},
                )
            )
        out.append((line, sr, tentative))
    return out


async def approve(
    session: AsyncSession,
    user: User,
    rec_id: uuid.UUID,
    reason: str | None,
    *,
    now: datetime | None = None,
) -> tuple[Recommendation, list[uuid.UUID], uuid.UUID | None]:
    """§7 step 5. Transfer: holds FIRM, requests CONFIRMED, one shipment per source. Buy: a
    SENT purchase order. Then the shortage -> IN_FULFILLMENT. Returns the recommendation,
    the shipment ids and the purchase order id."""
    now = now or datetime.now(UTC)
    rec, shortage = await _decidable(session, user, rec_id, RecStatus.APPROVED, now)
    typed = _typed(reason)
    confirm = [] if rec.type == BUY else await _requests_to_confirm(session, shortage, rec, now)
    await transitions.move(
        session, shortage, rec, RecStatus.APPROVED, user, typed, **_decision(user, typed, now)
    )
    shipment_ids: list[uuid.UUID] = []
    po_id = None
    for line, sr, tentative in confirm:
        for hold in tentative:
            # The hold's row is in the source org: SYSTEM with the factual cause, never the
            # approver's id or text (business-rules.md §10).
            await holds.move_hold(
                session, hold, sr.source_org_id, HoldStatus.FIRM, None, holds.REQUESTER_APPROVED
            )
        await holds.move_request(session, shortage, sr, RequestStatus.CONFIRMED, user, typed)
        shipment = await shipments.create(
            session,
            shortage,
            from_org_id=sr.source_org_id,
            qty=sr.qty,
            planned_eta=now + timedelta(hours=line["eta_hours"]),
            actor=user,
            reason=typed,
            source_request_id=sr.id,
        )
        shipment_ids.append(shipment.id)
    if rec.type == BUY:
        (line,) = rec.lines
        po_id = (await purchase_orders.create(session, shortage, line, user, typed, now)).id
    await shortages.move_shortage(session, shortage, Status.IN_FULFILLMENT, user, typed)
    return rec, shipment_ids, po_id


async def reject(
    session: AsyncSession,
    user: User,
    rec_id: uuid.UUID,
    reason: str | None,
    *,
    now: datetime | None = None,
) -> Recommendation:
    """REJECTED (reason optional), then end the requests, release every hold and re-run
    matching (§7 step 6) without the rejected plan's sources (its hospital lines, or the
    supplier of a BUY), which stay out of this shortage's later runs as a decline does.
    The shortage goes AWAITING_DECISION -> MATCHING."""
    now = now or datetime.now(UTC)
    rec, shortage = await _decidable(session, user, rec_id, RecStatus.REJECTED, now)
    typed = _typed(reason)
    await transitions.move(
        session, shortage, rec, RecStatus.REJECTED, user, typed, **_decision(user, typed, now)
    )
    await _end_requests(session, shortage, rules.REJECTED)
    await source_requests.release_and_rematch(
        session,
        shortage,
        rules.REJECTED,
        trigger=Trigger.MANUAL,
        exclude=rejected_sources(rec),
        now=now,
    )
    return rec


def rejected_sources(rec: Recommendation) -> list[uuid.UUID]:
    """The orgs of a rejected recommendation's lines (§7 step 6)."""
    return sorted({uuid.UUID(str(line["source_org_id"])) for line in rec.lines}, key=str)


async def escalate(
    session: AsyncSession,
    user: User,
    rec_id: uuid.UUID,
    reason: str | None,
    *,
    now: datetime | None = None,
) -> Recommendation:
    """PENDING -> ESCALATED, and a Notification for every APPROVER of the org (§8). The
    recommendation keeps its holds and validity; any approver can still decide."""
    now = now or datetime.now(UTC)
    rec, shortage = await _decidable(session, user, rec_id, RecStatus.ESCALATED, now)
    typed = _typed(reason)
    await transitions.move(session, shortage, rec, RecStatus.ESCALATED, user, typed)
    await notifications.notify_role(
        session,
        shortage.org_id,
        RoleName.APPROVER,
        ESCALATED_NOTIFICATION,
        {
            "recommendation_id": rec.id,
            "shortage_id": shortage.id,
            "escalated_by": user.id,
            "reason": typed,
            "expires_at": rec.expires_at,
        },
    )
    return rec
