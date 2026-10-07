"""Shortages and the matching engine (business-rules.md §1, §3-§5, §7-§8). Every shortage
transition and match run writes an audit row in the caller's transaction; routers commit."""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import groupby
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.auth.models import User
from app.catalog.models import Product, ProductAuthorization, SupplierOffer
from app.domain import config, gates
from app.domain.costing import (
    HaversineProvider,
    Point,
    RoutingProvider,
    transport_cost_paise,
    transport_eta_hours,
)
from app.domain.gates import PASS, GateResult
from app.domain.inventory import batch_transferable, days_to_expiry_at
from app.domain.ranking import Option, is_near_expiry, rank
from app.domain.resolution import TRANSFER_SPLIT, Line, Plan, plan
from app.domain.shortage import TRANSITIONS, Status, shortfall
from app.domain.state_machine import InvalidTransition, transition
from app.errors import AppError
from app.inventory.models import InventoryBatch
from app.inventory.service import own_facility
from app.orgs.models import Facility, Organization, OrgStatus, OrgType
from app.shortages.models import (
    Candidate,
    MatchRun,
    Priority,
    Shortage,
    ShortageSource,
    SourceType,
    Trigger,
)
from app.shortages.schemas import NO_ELIGIBLE_SOURCE, MatchRunOut, PlannedResolution, ShortageCreate

ENTITY = "shortage"
SNAPSHOT = (
    "facility_id",
    "product_id",
    "qty_required",
    "qty_local_usable",
    "shortfall",
    "required_by",
    "priority",
    "min_shelf_life_days",
    "status",
    "notes",
    "source",
)
GATE_ORDER = (
    "product",
    "quantity",
    "shelf_life",
    "authorization",
    "freshness",
    "deadline",
    "cold_chain",
)
ROUTING: RoutingProvider = HaversineProvider()  # S11 adds OSRM, falling back to haversine
# ponytail: there is no Vehicle table until S11, so no cold-chain vehicle is on record and
# every cold-chain product fails the cold_chain gate; S11 replaces this with a Vehicle query.
COLD_CHAIN_VEHICLE_ON_RECORD = False


async def get_shortage(
    session: AsyncSession, user: User, shortage_id: uuid.UUID, *, lock: bool = False
) -> Shortage:
    """The caller's own org's shortage; `lock` serializes state changes (FOR UPDATE)."""
    shortage = await session.get(Shortage, shortage_id, with_for_update=lock)
    if shortage is None:
        raise AppError(404, "not_found", "Shortage not found.")
    if shortage.org_id != user.org_id:
        raise AppError(403, "forbidden", "This shortage belongs to another organization.")
    return shortage


async def create_shortage(
    session: AsyncSession, user: User, body: ShortageCreate, *, now: datetime | None = None
) -> Shortage:
    """OPEN with the hub-computed shortfall, then MATCHING with its first match run (§7)."""
    await own_facility(session, user, body.facility_id)
    product = await session.get(Product, body.product_id)
    if product is None:
        raise AppError(400, "validation", "Unknown product.", {"product_id": str(body.product_id)})
    gap = shortfall(body.qty_required, body.qty_local_usable)
    if gap == 0:  # §1: nothing to source, so no shortage and no match
        raise AppError(
            400,
            "validation",
            "Nothing to source: local usable stock covers the requirement.",
            {"qty_required": body.qty_required, "qty_local_usable": body.qty_local_usable},
        )
    min_days = body.min_shelf_life_days
    shortage = Shortage(
        org_id=user.org_id,
        facility_id=body.facility_id,
        product_id=product.id,
        qty_required=body.qty_required,
        qty_local_usable=body.qty_local_usable,
        shortfall=gap,
        required_by=body.required_by,
        priority=body.priority,
        min_shelf_life_days=product.default_min_shelf_life_days if min_days is None else min_days,
        status=Status.OPEN,
        notes=body.notes,
        created_by=user.id,
        source=ShortageSource.FORM,
    )
    session.add(shortage)
    await session.flush()
    after = {f: getattr(shortage, f) for f in SNAPSHOT}
    await audit.record(
        session, user, ENTITY, shortage.id, f"{ENTITY}.created", None, after, body.reason
    )
    await run_match(session, shortage, Trigger.CREATE, reason="Shortage created.", now=now)
    return shortage


async def cancel_shortage(
    session: AsyncSession, user: User, shortage_id: uuid.UUID, reason: str | None
) -> Shortage:
    """From OPEN, MATCHING or AWAITING_DECISION only; anything else is a 409.
    Releasing holds arrives with them (S06)."""
    shortage = await get_shortage(session, user, shortage_id, lock=True)
    await _move(session, shortage, Status.CANCELLED, user, reason)
    return shortage


async def rerun_match(
    session: AsyncSession,
    user: User,
    shortage_id: uuid.UUID,
    reason: str | None,
    *,
    now: datetime | None = None,
) -> MatchRun:
    """A manual re-run, while the shortage is OPEN or MATCHING (§8 lists no other way in)."""
    shortage = await get_shortage(session, user, shortage_id, lock=True)
    if shortage.status not in (Status.OPEN, Status.MATCHING):
        raise InvalidTransition(shortage.status, Status.MATCHING)
    return await run_match(session, shortage, Trigger.MANUAL, actor=user, reason=reason, now=now)


async def _move(
    session: AsyncSession, shortage: Shortage, to: Status, actor: User | None, reason: str | None
) -> None:
    before = transition(shortage, to, TRANSITIONS)
    await session.flush()
    await session.refresh(shortage)  # updated_at is set by the database
    await audit.record(
        session,
        actor,
        ENTITY,
        shortage.id,
        f"{ENTITY}.status_changed",
        {"status": before},
        {"status": shortage.status},
        reason,
        org_id=shortage.org_id,
    )


async def latest_run(session: AsyncSession, shortage_id: uuid.UUID) -> MatchRun | None:
    stmt = select(MatchRun).where(MatchRun.shortage_id == shortage_id)
    return await session.scalar(stmt.order_by(MatchRun.run_no.desc()).limit(1))


async def match_run_out(session: AsyncSession, run: MatchRun) -> MatchRunOut:
    stmt = (
        select(Candidate, Organization.name)
        .join(Organization, Organization.id == Candidate.source_org_id)
        .where(Candidate.match_run_id == run.id)
        .order_by(Candidate.rank.asc().nulls_last(), Organization.name, Candidate.id)
    )
    return MatchRunOut.of(run, [(c, name) for c, name in await session.execute(stmt)])


# --- matching ------------------------------------------------------------------------------


@dataclass
class _Source:
    """A source org as matching sees it. Hospital `results` get their quantity gate only once
    the plan is known (a split candidate needs just qty > 0, §3)."""

    org_id: uuid.UUID
    type: SourceType
    option: Option
    results: dict[str, GateResult]
    batch_ids: list[uuid.UUID] = field(default_factory=list)
    rank: int | None = None

    @property
    def eligible(self) -> bool:
        return all(r.passed for r in self.results.values())


@dataclass(frozen=True)
class _Lot:
    row: Any  # the batch columns matching needs
    km: float
    eta_hours: float
    transferable: int
    days_at_delivery: int


async def run_match(
    session: AsyncSession,
    shortage: Shortage,
    trigger: Trigger,
    *,
    actor: User | None = None,
    reason: str | None = None,
    exclude: Iterable[uuid.UUID] = (),
    now: datetime | None = None,
) -> MatchRun:
    """Check every other hospital holding the product and every supplier offering it against
    every gate (§3), rank the eligible ones (§5) and store the run with its planned resolution.
    `exclude` orgs (e.g. a source that declined) stay out of this shortage's later runs too.
    A system run (no actor) passes its factual cause as `reason`."""
    now = now or datetime.now(UTC)
    if shortage.status == Status.OPEN:
        await _move(session, shortage, Status.MATCHING, actor, reason)
    last = await latest_run(session, shortage.id)
    excluded = sorted({*(last.excluded_org_ids if last else ()), *exclude})
    sources = await _sources(session, shortage, excluded, now)
    planned = _decide(sources, shortage)

    run = MatchRun(
        id=uuid.uuid4(),
        shortage_id=shortage.id,
        run_no=last.run_no + 1 if last else 1,
        triggered_by=trigger,
        ts=now,
        excluded_org_ids=excluded,
    )
    candidates = [_candidate(s, run.id, shortage.shortfall) for s in sources]
    ids = {str(c.source_org_id): c.id for c in candidates}
    run.planned_resolution = _plan_json(planned, ids)
    session.add(run)
    await session.flush()
    session.add_all(candidates)
    await session.flush()

    eligible = sum(c.eligible for c in candidates)
    after = {
        "shortage_id": shortage.id,
        "run_no": run.run_no,
        "triggered_by": trigger,
        "excluded_org_ids": excluded,
        "eligible": eligible,
        "rejected": len(candidates) - eligible,
        "result": planned.type if planned else NO_ELIGIBLE_SOURCE,
    }
    await audit.record(
        session,
        actor,
        "match_run",
        run.id,
        "match_run.created",
        None,
        after,
        reason,
        org_id=shortage.org_id,
    )
    return run


async def _sources(
    session: AsyncSession, shortage: Shortage, excluded: list[uuid.UUID], now: datetime
) -> list[_Source]:
    """Load only what matching needs and apply every gate but the hospital quantity gate."""
    product = await session.get_one(Product, shortage.product_id)
    to = await session.get_one(Facility, shortage.facility_id)
    dest = Point(to.lat, to.lng)
    authorized = set(
        await session.scalars(
            select(ProductAuthorization.org_id).where(
                ProductAuthorization.product_id == shortage.product_id
            )
        )
    )
    need, min_days, today = shortage.shortfall, shortage.min_shelf_life_days, now.date()
    critical = shortage.priority == Priority.CRITICAL
    max_age = config.VERIFIED_WITHIN_CRITICAL if critical else config.VERIFIED_WITHIN_ROUTINE

    batches = await session.execute(
        select(
            InventoryBatch.id,
            InventoryBatch.org_id,
            InventoryBatch.product_id,
            InventoryBatch.on_hand,
            InventoryBatch.reserved,
            InventoryBatch.allocated,
            InventoryBatch.safety_stock,
            InventoryBatch.quarantined,
            InventoryBatch.expiry_date,
            InventoryBatch.unit_cost_paise,
            InventoryBatch.last_verified_at,
            Facility.lat,
            Facility.lng,
            Facility.has_cold_storage,
            Organization.status,
        )
        .join(Facility, Facility.id == InventoryBatch.facility_id)
        .join(Organization, Organization.id == InventoryBatch.org_id)
        .where(
            InventoryBatch.product_id == shortage.product_id,
            Organization.type == OrgType.HOSPITAL,
            Organization.id != shortage.org_id,
            Organization.id.not_in(excluded),
        )
        .order_by(InventoryBatch.org_id)
    )
    sources: list[_Source] = []
    for org_id, rows in groupby(batches, key=lambda r: r.org_id):
        lots = []
        for r in rows:
            km = await ROUTING.distance_km(Point(r.lat, r.lng), dest)
            eta = transport_eta_hours(km)
            arrival = (now + timedelta(hours=eta)).date()
            # ponytail: holds arrive in S06; until then nothing is held (held_qty=0).
            qty = batch_transferable(r, today)
            lots.append(_Lot(r, km, eta, qty, days_to_expiry_at(r, arrival)))
        stocked = [x for x in lots if x.transferable > 0]
        qualifying = [x for x in stocked if x.days_at_delivery >= min_days]  # §2
        # If nothing qualifies, shelf_life fails and quantity judges the stock as recorded.
        counted = sorted(qualifying or stocked, key=lambda x: (x.row.expiry_date, x.row.id))
        basis = counted or lots
        far = max(basis, key=lambda x: x.km)  # one trip per source, from its farthest store
        verified = [x.row.last_verified_at for x in basis]
        oldest = None if None in verified else min(verified)
        first = basis[0].row
        sources.append(
            _Source(
                org_id=org_id,
                type=SourceType.HOSPITAL,
                option=Option(
                    key=str(org_id),
                    hospital=True,
                    qty=sum(x.transferable for x in counted),
                    eta_hours=far.eta_hours,
                    reliability=config.DEFAULT_RELIABILITY,  # ReliabilityScore arrives in S19
                    near_expiry=bool(counted) and is_near_expiry(counted[0].row.expiry_date, today),
                    lots=tuple((x.transferable, x.row.unit_cost_paise) for x in counted),
                    transport_paise=transport_cost_paise(far.km),
                ),
                results={
                    "product": gates.product(first.product_id, shortage.product_id),
                    "shelf_life": (
                        gates.shelf_life(max(x.days_at_delivery for x in stocked), min_days)
                        if stocked
                        else PASS
                    ),
                    "authorization": gates.authorization(
                        first.status == OrgStatus.ACTIVE, org_id in authorized
                    ),
                    "freshness": gates.freshness(oldest, now, max_age),
                    "deadline": gates.deadline(
                        now + timedelta(hours=far.eta_hours), shortage.required_by
                    ),
                    "cold_chain": gates.cold_chain(
                        product.requires_cold_chain,
                        all(x.row.has_cold_storage for x in basis),
                        COLD_CHAIN_VEHICLE_ON_RECORD,
                    ),
                },
                batch_ids=[x.row.id for x in counted],
            )
        )

    offers = await session.execute(
        select(
            SupplierOffer.org_id,
            SupplierOffer.product_id,
            SupplierOffer.unit_price_paise,
            SupplierOffer.lead_time_hours,
            SupplierOffer.available_qty,
            SupplierOffer.updated_at,
            Organization.lat,
            Organization.lng,
            Organization.status,
        )
        .join(Organization, Organization.id == SupplierOffer.org_id)
        .where(
            SupplierOffer.product_id == shortage.product_id,
            Organization.type == OrgType.SUPPLIER,
            Organization.id.not_in(excluded),
        )
    )
    for o in offers:
        km = await ROUTING.distance_km(Point(o.lat, o.lng), dest)
        eta = o.lead_time_hours + transport_eta_hours(km)
        sources.append(
            _Source(
                org_id=o.org_id,
                type=SourceType.SUPPLIER,
                option=Option(
                    key=str(o.org_id),
                    hospital=False,
                    qty=o.available_qty,
                    eta_hours=eta,
                    reliability=config.DEFAULT_RELIABILITY,
                    near_expiry=False,
                    lots=((o.available_qty, o.unit_price_paise),),
                    transport_paise=transport_cost_paise(km),
                ),
                results={
                    "product": gates.product(o.product_id, shortage.product_id),
                    "quantity": gates.quantity(o.available_qty, need, noun="available"),
                    "shelf_life": PASS,  # new stock (§3)
                    "authorization": gates.authorization(
                        o.status == OrgStatus.ACTIVE, o.org_id in authorized
                    ),
                    "freshness": gates.freshness(
                        o.updated_at, now, config.OFFER_UPDATED_WITHIN, what="Offer last updated"
                    ),
                    "deadline": gates.deadline(now + timedelta(hours=eta), shortage.required_by),
                    "cold_chain": gates.cold_chain(
                        product.requires_cold_chain, True, COLD_CHAIN_VEHICLE_ON_RECORD
                    ),
                },
            )
        )
    return sources


def _decide(sources: list[_Source], shortage: Shortage) -> Plan | None:
    """Plan from the sources passing every other gate (§5), then judge hospital quantity:
    each must cover the shortfall alone unless the plan is a split (§3). Rank the eligible."""
    need, critical = shortage.shortfall, shortage.priority == Priority.CRITICAL
    hospitals = [s for s in sources if s.type == SourceType.HOSPITAL]
    suppliers = [s for s in sources if s.type == SourceType.SUPPLIER]
    # Hospital results have no quantity gate yet, so `eligible` means "every other gate".
    planned = plan(
        [s.option for s in hospitals if s.eligible],
        [s.option for s in suppliers if s.eligible],
        need,
        critical,
    )
    split = planned is not None and planned.type == TRANSFER_SPLIT
    for s in hospitals:
        s.results["quantity"] = gates.quantity(s.option.qty, need, split=split)
    ranked = rank([s.option for s in sources if s.eligible], need, critical)
    position = {o.key: n for n, o in enumerate(ranked, 1)}
    for s in sources:
        s.rank = position.get(s.option.key)
    return planned


def _candidate(s: _Source, run_id: uuid.UUID, need: int) -> Candidate:
    hospital = s.type == SourceType.HOSPITAL
    return Candidate(
        id=uuid.uuid4(),
        match_run_id=run_id,
        source_org_id=s.org_id,
        source_type=s.type,
        batch_ids=s.batch_ids,
        transferable_qty=s.option.qty if hospital else None,
        offered_qty=None if hospital else s.option.qty,
        gate_results=[
            {"gate": g, "passed": s.results[g].passed, "reason": s.results[g].reason}
            for g in GATE_ORDER
        ],
        eligible=s.eligible,
        landed_cost_paise=s.option.cost(need) if s.eligible else None,
        eta_hours=s.option.eta_hours,
        reliability=s.option.reliability,
        rank=s.rank,
    )


def _plan_json(planned: Plan | None, ids: dict[str, uuid.UUID]) -> dict[str, Any] | None:
    if planned is None:
        return None

    def line(x: Line) -> dict[str, Any]:
        o = x.option
        source_type = SourceType.HOSPITAL if o.hospital else SourceType.SUPPLIER
        return {
            "candidate_id": ids[o.key],
            "source_org_id": o.key,
            "source_type": source_type,
            "qty": x.qty,
            "landed_cost_paise": x.landed_cost_paise,
            "eta_hours": o.eta_hours,
        }

    lines, alternatives = [line(x) for x in planned.lines], [line(x) for x in planned.alternatives]
    out = {"type": planned.type, "lines": lines, "alternatives": alternatives}
    return PlannedResolution.model_validate(out).model_dump(mode="json")
