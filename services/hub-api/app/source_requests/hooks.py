"""Hook points for later sections. Kept in their own module so matching and the request
service can both call them without importing each other."""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.shortages.models import MatchRun, Shortage


async def on_sources_ready(
    session: AsyncSession, shortage: Shortage, run: MatchRun, *, now: datetime | None = None
) -> None:
    """Called once every request of `run`'s plan is TENTATIVE_HOLD, and right after a run
    whose plan is BUY (business-rules.md §7 step 4: "or immediately for BUY").

    S09: creates the Recommendation (valid for config.RECOMMENDATION_VALIDITY) and moves the
    shortage MATCHING -> AWAITING_DECISION."""
    # Imported here: the recommendation service imports matching and the request service,
    # which both import this module.
    from app.recommendations import service as recommendations

    await recommendations.create(session, shortage, run, now=now)
