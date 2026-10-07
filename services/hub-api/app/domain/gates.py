"""Eligibility gates (business-rules.md §3). One pure function per gate; a failure carries
the plain-language reason users see. A candidate must pass every gate."""

import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reason: str | None = None


PASS = GateResult(True)


def _fail(reason: str) -> GateResult:
    return GateResult(False, reason)


def _days(n: int) -> str:
    return f"{n} day" if n == 1 else f"{n} days"


def _age(age: timedelta) -> str:
    hours = int(age.total_seconds() // 3600)
    return f"{hours} h" if hours < 48 else _days(age.days)


def product(source_product_id: uuid.UUID, wanted_product_id: uuid.UUID) -> GateResult:
    if source_product_id == wanted_product_id:
        return PASS
    return _fail("Different product specification")


def quantity(
    qty: int, needed: int, *, split: bool = False, noun: str = "transferable"
) -> GateResult:
    """A single source must cover `needed`; a split candidate needs only qty > 0."""
    if qty > 0 if split else qty >= needed:
        return PASS
    return _fail(f"Only {qty} {noun}; {needed} needed")


def shelf_life(days_at_delivery: int, min_days: int) -> GateResult:
    """days_at_delivery = (expiry_date - estimated arrival date).days (§2)."""
    if days_at_delivery >= min_days:
        return PASS
    if days_at_delivery <= 0:
        return _fail(f"Expires before delivery; {min_days} required")
    return _fail(f"Expires in {_days(days_at_delivery)}; {min_days} required")


def authorization(org_active: bool, authorized: bool) -> GateResult:
    if not org_active:
        return _fail("Organization is suspended")
    if not authorized:
        return _fail("Not authorized to supply this product")
    return PASS


def freshness(
    last: datetime | None, now: datetime, max_age: timedelta, what: str = "Stock last verified"
) -> GateResult:
    """Hospital: oldest last_verified_at of the stock counted. Supplier: offer updated_at."""
    if last is None:
        return _fail("Stock has never been verified")
    if now - last <= max_age:
        return PASS
    return _fail(f"{what} {_age(now - last)} ago")


def deadline(arrival: datetime, required_by: datetime) -> GateResult:
    if arrival <= required_by:
        return PASS
    late = math.ceil((arrival - required_by).total_seconds() / 3600)
    return _fail(f"Arrives {late} h after the deadline")


def cold_chain(requires: bool, cold_storage: bool, vehicle: bool) -> GateResult:
    """Supplier sources pass cold_storage=True: only the vehicle applies to them."""
    if not requires:
        return PASS
    if not cold_storage:
        return _fail("No cold storage at the source facility")
    if not vehicle:
        return _fail("No cold-chain transport available")
    return PASS
