import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from app.db import NonNegInt4, NulFreeStr
from app.domain.shortage import Status
from app.shortages.models import Candidate, MatchRun, Priority, ShortageSource, SourceType, Trigger

NO_ELIGIBLE_SOURCE = "No eligible source"


class ShortageCreate(BaseModel):
    """Any unknown field is a 422, except `shortfall`: the hub computes it
    (business-rules.md §1), so a client value is accepted and ignored."""

    model_config = ConfigDict(extra="forbid")

    facility_id: uuid.UUID
    product_id: uuid.UUID
    qty_required: NonNegInt4
    qty_local_usable: NonNegInt4
    required_by: AwareDatetime
    priority: Priority
    min_shelf_life_days: NonNegInt4 | None = Field(
        default=None, description="Defaults to the product's default_min_shelf_life_days."
    )
    notes: NulFreeStr | None = None
    reason: NulFreeStr | None = None
    shortfall: SkipJsonSchema[Any] = Field(default=None, exclude=True)


class ReasonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: NulFreeStr | None = None


class ShortageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    facility_id: uuid.UUID
    product_id: uuid.UUID
    qty_required: int
    qty_local_usable: int
    shortfall: int = Field(
        json_schema_extra={"readOnly": True},
        description="Hub-computed max(0, qty_required - qty_local_usable); never taken as input.",
    )
    required_by: datetime
    priority: Priority
    min_shelf_life_days: int
    status: Status
    notes: str | None
    parent_shortage_id: uuid.UUID | None
    created_by: uuid.UUID
    source: ShortageSource
    created_at: datetime
    updated_at: datetime


class GateOut(BaseModel):
    gate: str
    passed: bool
    reason: str | None


class CandidateOut(BaseModel):
    """What the requester's org sees of a source: no batch ids, raw stock figures or, for a
    hospital source, cost (it would reveal that hospital's unit cost; CLAUDE.md rule 6)."""

    id: uuid.UUID
    source_org_id: uuid.UUID
    source_org_name: str
    source_type: SourceType
    transferable_qty: int | None = Field(description="Hospital sources: hub-computed.")
    offered_qty: int | None = Field(description="Supplier sources: the offer's available qty.")
    gate_results: list[GateOut]
    eligible: bool
    landed_cost_paise: int | None = Field(
        description="Eligible supplier candidates only; never shown for a hospital source."
    )
    eta_hours: float
    reliability: int
    rank: int | None = Field(description="1 = best; eligible candidates only.")

    @classmethod
    def of(cls, c: Candidate, org_name: str) -> "CandidateOut":
        fields = {f: getattr(c, f) for f in cls.model_fields if f != "source_org_name"}
        if c.source_type == SourceType.HOSPITAL:
            fields["landed_cost_paise"] = None
        return cls(**fields, source_org_name=org_name)


class PlanLine(BaseModel):
    candidate_id: uuid.UUID
    source_org_id: uuid.UUID
    source_type: SourceType
    qty: int
    landed_cost_paise: int | None = Field(description="Supplier lines only, as for candidates.")
    eta_hours: float


class PlannedResolution(BaseModel):
    type: Literal["TRANSFER", "TRANSFER_SPLIT", "BUY"]
    lines: list[PlanLine]
    alternatives: list[PlanLine] = Field(description="The best BUY, or for a BUY the next one.")
    parallel: list[PlanLine] = Field(
        default_factory=list,
        description="CRITICAL TRANSFER only (S19): the single-source candidates asked at once, "
        "the planned line first; the first to accept wins. Empty for every other plan.",
    )


class MatchRunOut(BaseModel):
    id: uuid.UUID
    shortage_id: uuid.UUID
    run_no: int
    triggered_by: Trigger
    ts: datetime
    excluded_org_ids: list[uuid.UUID]
    planned_resolution: PlannedResolution | None
    reason: str | None = Field(description='"No eligible source" when nothing is eligible.')
    candidates: list[CandidateOut] = Field(description="Eligible by rank, then rejected.")

    @staticmethod
    def _shown(plan: dict[str, Any]) -> PlannedResolution:
        """The stored plan keeps every cost; the requester sees supplier costs only."""
        out = PlannedResolution.model_validate(plan)
        for line in (*out.lines, *out.alternatives, *out.parallel):
            if line.source_type == SourceType.HOSPITAL:
                line.landed_cost_paise = None
        return out

    @classmethod
    def of(cls, run: MatchRun, candidates: Iterable[tuple[Candidate, str]]) -> "MatchRunOut":
        plan = run.planned_resolution
        return cls(
            id=run.id,
            shortage_id=run.shortage_id,
            run_no=run.run_no,
            triggered_by=Trigger(run.triggered_by),
            ts=run.ts,
            excluded_org_ids=run.excluded_org_ids,
            planned_resolution=cls._shown(plan) if plan else None,
            reason=None if plan else NO_ELIGIBLE_SOURCE,
            candidates=[CandidateOut.of(c, name) for c, name in candidates],
        )
