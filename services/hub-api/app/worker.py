"""arq worker for the hub's timers (business-rules.md §6: response, hold and recommendation
deadlines; §11: silent cold-chain devices; S18: surplus expiry), the nightly forecast and
surplus matching (S18), the nightly reliability recompute (§12), the event publisher, the
hourly outbox pruning (S20) and webhook deliveries (api-and-events.md, Events and Webhooks).
Run it with `make worker`; the apps get live updates only while it runs.

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

from app.coldchain import service as coldchain
from app.config import settings
from app.db import SessionLocal, engine
from app.dbrole import require_least_privilege
from app.domain import config
from app.events import service as events
from app.events import webhooks
from app.forecasting import service as forecasting
from app.recommendations import service as recommendations
from app.shortages import service as shortages
from app.source_requests import service as source_requests
from app.surplus import service as surplus
from app.trust import service as trust

WEBHOOK_INTERVAL_SECONDS = 5


async def startup(ctx: dict[str, Any]) -> None:
    if "sessionmaker" not in ctx:
        # S20: outside dev, refuse a database role that could rewrite the audit log.
        await require_least_privilege(engine)
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


async def check_silent_devices(ctx: dict[str, Any]) -> int:
    """Raise DEVICE_SILENT for IN_TRANSIT shipments whose cold box went quiet (§11)."""
    sessionmaker: async_sessionmaker[AsyncSession] = ctx["sessionmaker"]
    async with sessionmaker() as session:
        return await coldchain.check_silent_devices(session)


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


async def prune_events(ctx: dict[str, Any]) -> int:
    """S20: drop published outbox events past their retention (EVENT_RETENTION_HOURS;
    `coldchain.reading` READING_EVENT_RETENTION_HOURS) with their finished deliveries."""
    sessionmaker: async_sessionmaker[AsyncSession] = ctx["sessionmaker"]
    async with sessionmaker() as session:
        return await events.prune(session)


async def deliver_webhooks(ctx: dict[str, Any]) -> int:
    sessionmaker: async_sessionmaker[AsyncSession] = ctx["sessionmaker"]
    async with sessionmaker() as session:
        return await webhooks.deliver_due(session, ctx["http"])


async def expire_surplus(ctx: dict[str, Any]) -> int:
    """Expire surplus posts whose batch expiry date has come (S18); returns how many."""
    sessionmaker: async_sessionmaker[AsyncSession] = ctx["sessionmaker"]
    async with sessionmaker() as session:
        count = await surplus.expire_due(session, forecasting.today())
        await session.commit()
        return count


async def nightly_forecasts(ctx: dict[str, Any]) -> int:
    """Forecast every hospital (statsmodels, committed per org), then expire and match every
    live surplus post (S18). Returns the number of new surplus matches."""
    sessionmaker: async_sessionmaker[AsyncSession] = ctx["sessionmaker"]
    day = forecasting.today()
    async with sessionmaker() as session:
        await forecasting.run(session, await forecasting.hospital_org_ids(session), day)
        await surplus.expire_due(session, day)
        count = await surplus.match_open(session, day)
        await session.commit()
        return count


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
            check_silent_devices,
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
            expire_surplus,
            second=set(range(0, 60, config.TIMER_INTERVAL_SECONDS)),
            run_at_startup=True,
            unique=True,
        ),
        cron(
            nightly_forecasts,
            hour={config.FORECAST_NIGHTLY_HOUR_UTC},
            minute={config.FORECAST_NIGHTLY_MINUTE_UTC},
            second={0},
            unique=True,
            timeout=60 * 60,  # every hospital x product fit; minutes, not seconds
        ),
        cron(prune_events, minute=5, second=0, run_at_startup=True, unique=True, timeout=10 * 60),
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
