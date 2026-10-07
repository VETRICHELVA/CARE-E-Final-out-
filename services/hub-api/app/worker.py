"""arq worker for the hub's timers (business-rules.md §6). Run it with `make worker`.

Deadlines are stored in the database, so a restarted worker loses nothing: the job runs at
startup and then every TIMER_INTERVAL_SECONDS. It is idempotent and safe with several
workers (each request is re-checked under its row locks before it is expired)."""

from typing import Any

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.db import SessionLocal
from app.domain import config
from app.source_requests import service as source_requests


async def startup(ctx: dict[str, Any]) -> None:
    ctx.setdefault("sessionmaker", SessionLocal)


async def expire_source_requests(ctx: dict[str, Any]) -> int:
    """Expire overdue REQUESTED and TENTATIVE_HOLD requests; returns how many."""
    sessionmaker: async_sessionmaker[AsyncSession] = ctx["sessionmaker"]
    async with sessionmaker() as session:
        return await source_requests.expire_overdue(session)


class WorkerSettings:
    functions: list[Any] = []
    cron_jobs = [
        cron(
            expire_source_requests,
            second=set(range(0, 60, config.TIMER_INTERVAL_SECONDS)),
            run_at_startup=True,
            unique=True,  # one run per tick, however many workers are up
        )
    ]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
