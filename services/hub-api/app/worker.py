"""arq worker for the hub's timers (business-rules.md §6: response, hold and recommendation
deadlines), the nightly reliability recompute (§12), the event publisher and webhook
deliveries (api-and-events.md, Events and Webhooks). Run it with `make worker`; the apps get
live updates only while it runs.

Deadlines, unpublished events and due deliveries are all stored in the database, so a
restarted worker loses nothing. Every job is idempotent and safe with several workers: the
timer re-checks each request under its row locks, the publisher takes an advisory lock, and
deliveries are claimed with SKIP LOCKED."""

from typing import Any

import httpx
from arq import cron
from arq.connections import RedisSettings
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.db import SessionLocal
from app.domain import config
from app.events import service as events
from app.events import webhooks
from app.recommendations import service as recommendations
from app.shortages import service as shortages
from app.source_requests import service as source_requests
from app.trust import service as trust

WEBHOOK_INTERVAL_SECONDS = 5


async def startup(ctx: dict[str, Any]) -> None:
    ctx.setdefault("sessionmaker", SessionLocal)
    # Never follow redirects: a webhook URL is called exactly as the org admin entered it.
    ctx.setdefault("http", httpx.AsyncClient(follow_redirects=False))


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["http"].aclose()


async def expire_source_requests(ctx: dict[str, Any]) -> int:
    """Expire overdue REQUESTED and TENTATIVE_HOLD requests; returns how many."""
    sessionmaker: async_sessionmaker[AsyncSession] = ctx["sessionmaker"]
    async with sessionmaker() as session:
        return await source_requests.expire_overdue(session)


async def rematch_after_releases(ctx: dict[str, Any]) -> int:
    """Re-run shortages waiting on "No eligible source" once held stock is released (§5)."""
    sessionmaker: async_sessionmaker[AsyncSession] = ctx["sessionmaker"]
    async with sessionmaker() as session:
        count = await shortages.rematch_after_releases(session)
        await session.commit()
        return count


async def expire_recommendations(ctx: dict[str, Any]) -> int:
    """Expire PENDING and ESCALATED recommendations past their validity; returns how many."""
    sessionmaker: async_sessionmaker[AsyncSession] = ctx["sessionmaker"]
    async with sessionmaker() as session:
        return await recommendations.expire_overdue(session)


async def recompute_reliability(ctx: dict[str, Any]) -> int:
    """§12: recompute every hospital's and supplier's stored reliability score (nightly)."""
    sessionmaker: async_sessionmaker[AsyncSession] = ctx["sessionmaker"]
    async with sessionmaker() as session:
        return await trust.recompute_all(session)


async def publish_events(ctx: dict[str, Any]) -> int:
    """Publish committed outbox rows to Redis (SSE) and schedule their webhooks."""
    sessionmaker: async_sessionmaker[AsyncSession] = ctx["sessionmaker"]
    redis: Redis = ctx["redis"]
    async with sessionmaker() as session:
        return await events.publish_pending(session, redis)


async def deliver_webhooks(ctx: dict[str, Any]) -> int:
    sessionmaker: async_sessionmaker[AsyncSession] = ctx["sessionmaker"]
    async with sessionmaker() as session:
        return await webhooks.deliver_due(session, ctx["http"])


class WorkerSettings:
    functions: list[Any] = []
    cron_jobs = [
        cron(
            expire_source_requests,
            second=set(range(0, 60, config.TIMER_INTERVAL_SECONDS)),
            run_at_startup=True,
            unique=True,  # one run per tick, however many workers are up
        ),
        cron(
            rematch_after_releases,
            second=set(range(0, 60, config.TIMER_INTERVAL_SECONDS)),
            run_at_startup=True,
            unique=True,
        ),
        cron(
            expire_recommendations,
            second=set(range(0, 60, config.TIMER_INTERVAL_SECONDS)),
            run_at_startup=True,
            unique=True,
        ),
        cron(
            recompute_reliability,
            hour=config.RELIABILITY_NIGHTLY_HOUR_UTC,
            minute=0,
            second=0,
            unique=True,
        ),
        cron(publish_events, second=set(range(60)), run_at_startup=True, unique=True),
        cron(
            deliver_webhooks,
            second=set(range(0, 60, WEBHOOK_INTERVAL_SECONDS)),
            run_at_startup=True,
            unique=True,
        ),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
