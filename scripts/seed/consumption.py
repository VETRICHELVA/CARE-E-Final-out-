"""Synthetic consumption history for forecasting (S18; demo-scenarios.md, "Synthetic
consumption history"). Every row it writes is a ConsumptionRecord with `synthetic = true`:
no hospital has reported real consumption yet, and the apps say so.

- 12 months of daily records, ending yesterday (UTC), per hospital x product for the 15
  most-used products (TOP_PRODUCTS).
- Base rate by product and hospital size; weekly seasonality (weekdays 1.2x weekends); +-15%
  noise; 3-5 spikes of 2-3x per series. Fixed random seed: the same `today` always gives the
  same numbers.
- Scenario 3's two IV Cannula 20G series are generated so its numbers hold (no noise or
  spikes): Hospital B uses about 500 in the 55 days before its batch expires, and Hospital E
  about 30 a day, so its 120 usable run out in 4 days.

Run from services/hub-api, against the hub's database (DATABASE_URL):

    cd services/hub-api && uv run python ../../scripts/seed/consumption.py

Idempotent: it replaces each series' synthetic rows and never touches a reported (non-
synthetic) row. Hospitals not in the database are skipped (the dev seed has A and B only).
The hub's tests load the pure generator below with `runpy`.
"""

import asyncio
import random
import sys
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

SEED = 18
DAYS = 365
NOISE = 0.15  # +-15%
SPIKES = (3, 5)  # per series
SPIKE_FACTOR = (2.0, 3.0)
WEEKDAY_TO_WEEKEND = 1.2
# Weekday and weekend multipliers with a weekly mean of exactly 1.
WEEKDAY = 7 / (5 + 2 / WEEKDAY_TO_WEEKEND)
WEEKEND = WEEKDAY / WEEKDAY_TO_WEEKEND

# Hospital size: scales every base rate.
HOSPITAL_SIZES = {
    "Hospital A": 1.0,
    "Hospital B": 0.8,
    "Hospital C": 1.3,
    "Hospital D": 0.6,
    "Hospital E": 1.0,
    "Hospital F": 0.9,
}

# The 15 most-used products: units a day at a size-1.0 hospital.
TOP_PRODUCTS = {
    "INJ-SYR-5": 120,
    "PPE-GLV-NIT": 60,
    "SURG-GLV-STR": 45,
    "IV-CAN-20G": 30,
    "IV-SET-INF": 28,
    "IV-NS-500": 40,
    "PPE-MSK-3PLY": 25,
    "WND-GZE-STR": 35,
    "INJ-NDL-HYP": 18,
    "DIAG-GLU-STR": 15,
    "DIAG-EDTA-TUB": 12,
    "WND-TAP-MIC": 10,
    "IV-STC-3W": 9,
    "SURG-KIT-A": 8,
    "DIAG-RDK": 6,
}

# Scenario 3 (demo-scenarios.md): exact weekly patterns, Monday first.
# B: 64 a week (weekdays 9.6 on average, weekends 8: 1.2x), so any 55 days use 502-504.
# E: weekdays 30, weekends 25 (1.2x), about 28.6 a day: any 4 days use at most 120 and any 5
#    more, so 120 usable runs out on the 5th day from today ("in 4 days") whatever the weekday.
SCENARIO_3 = {
    ("Hospital B", "IV-CAN-20G"): (10, 9, 10, 9, 10, 8, 8),
    ("Hospital E", "IV-CAN-20G"): (30, 30, 30, 30, 30, 25, 25),
}


def history_days(today: date) -> list[date]:
    """The DAYS dates a series covers: up to and including yesterday."""
    return [today - timedelta(days=DAYS - i) for i in range(DAYS)]


def series(hospital: str, code: str, today: date) -> list[int]:
    """One hospital x product series, oldest day first (see history_days)."""
    days = history_days(today)
    pattern = SCENARIO_3.get((hospital, code))
    if pattern is not None:
        return [pattern[d.weekday()] for d in days]
    rng = random.Random(f"{SEED}:{hospital}:{code}")
    base = TOP_PRODUCTS[code] * HOSPITAL_SIZES[hospital]
    spikes = {i: rng.uniform(*SPIKE_FACTOR) for i in rng.sample(range(DAYS), rng.randint(*SPIKES))}
    out = []
    for i, d in enumerate(days):
        rate = base * (WEEKDAY if d.weekday() < 5 else WEEKEND)
        rate *= 1 + rng.uniform(-NOISE, NOISE)
        rate *= spikes.get(i, 1.0)
        out.append(max(0, round(rate)))
    return out


def rows(hospitals: list[str], today: date) -> Iterator[tuple[str, str, date, int]]:
    """(hospital, product code, date, qty) for every series of these hospitals."""
    days = history_days(today)
    for hospital in hospitals:
        for code in TOP_PRODUCTS:
            yield from (
                (hospital, code, d, q)
                for d, q in zip(days, series(hospital, code, today), strict=True)
            )


async def load(session: object, today: date) -> dict[str, int]:
    """Replace the synthetic history of every seeded hospital; returns rows per hospital."""
    from sqlalchemy import delete, select
    from sqlalchemy.dialects.postgresql import insert
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.catalog.models import Product
    from app.forecasting.models import ConsumptionRecord
    from app.orgs.models import Organization, OrgType

    assert isinstance(session, AsyncSession)
    orgs = {
        o.name: o.id
        for o in await session.scalars(
            select(Organization).where(
                Organization.type == OrgType.HOSPITAL,
                Organization.name.in_(HOSPITAL_SIZES),
            )
        )
    }
    products = {
        p.code: p.id
        for p in await session.scalars(select(Product).where(Product.code.in_(TOP_PRODUCTS)))
    }
    missing = set(TOP_PRODUCTS) - set(products)
    if missing:
        raise RuntimeError(f"Products missing from the catalog: {sorted(missing)}")
    counts: dict[str, int] = {}
    for hospital in sorted(orgs):
        await session.execute(
            delete(ConsumptionRecord).where(
                ConsumptionRecord.org_id == orgs[hospital],
                ConsumptionRecord.product_id.in_(products.values()),
                ConsumptionRecord.synthetic.is_(True),
            )
        )
        values = [
            {
                "org_id": orgs[h],
                "product_id": products[code],
                "date": d,
                "qty": qty,
                "synthetic": True,
            }
            for h, code, d, qty in rows([hospital], today)
        ]
        # A reported (non-synthetic) row for the same day wins.
        await session.execute(insert(ConsumptionRecord).on_conflict_do_nothing(), values)
        counts[hospital] = len(values)
    return counts


async def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "hub-api"))
    from app.db import SessionLocal

    today = datetime.now(UTC).date()
    async with SessionLocal() as session:
        counts = await load(session, today)
        await session.commit()
    for hospital, n in counts.items():
        print(f"{hospital}: {n} synthetic consumption records.")
    skipped = sorted(set(HOSPITAL_SIZES) - set(counts))
    if skipped:
        print(f"Not in the database, skipped: {', '.join(skipped)}.")
    print(f"{DAYS} days ending {today - timedelta(days=1)} (UTC), {len(TOP_PRODUCTS)} products.")


if __name__ == "__main__":
    asyncio.run(main())
