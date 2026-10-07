"""Hook points for later sections. Kept in their own module so matching and the request
service can both call them without importing each other."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.shortages.models import MatchRun, Shortage


async def on_sources_ready(session: AsyncSession, shortage: Shortage, run: MatchRun) -> None:
    """Called once every request of `run`'s plan is TENTATIVE_HOLD, and right after a run
    whose plan is BUY (business-rules.md §7 step 4: "or immediately for BUY").

    TODO(S09): create the Recommendation (validity from config.RECOMMENDATION_VALIDITY) and
    move the shortage MATCHING -> AWAITING_DECISION.
    """
    del session, shortage, run
