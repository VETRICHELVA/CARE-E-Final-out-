"""`make demo-reset` (S20): drop and recreate the local dev database, migrate it and load the
demo seed (app/seed.py), so every scenario starts from demo-scenarios.md's numbers.

It refuses anything but the local dev database: APP_ENV must be set to `dev` explicitly (unset
counts as production), DATABASE_URL and MIGRATION_DATABASE_URL must point at a loopback host,
and the database must be `care` (infra/docker-compose.yml). There is no flag to override this;
reset another database by hand. The database is dropped, recreated and migrated as the owner
(MIGRATION_DATABASE_URL, default DATABASE_URL); the seed runs as the hub would."""

import argparse
import asyncio
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings

DEV_DATABASE = "care"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
HUB_DIR = Path(__file__).resolve().parent.parent


def refusal(url: URL, app_env: str) -> str | None:
    """Why `url` may not be wiped, or None for the local dev database."""
    if app_env != "dev":
        shown = repr(app_env) if app_env else "unset"
        return f"APP_ENV is {shown}; demo-reset runs only with APP_ENV=dev set explicitly."
    if (url.host or "") not in LOOPBACK:
        return f"DATABASE_URL host is {url.host!r}; demo-reset wipes a local database only."
    if url.database != DEV_DATABASE:
        return (
            f"DATABASE_URL names database {url.database!r}; demo-reset wipes only the dev "
            f"database {DEV_DATABASE!r}."
        )
    return None


async def recreate(url: URL) -> None:
    """DROP DATABASE ... WITH (FORCE) ends open connections (a running hub reconnects)."""
    admin = create_async_engine(
        url.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    await admin.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-forecast", action="store_true", help="skip the forecast run")
    args = parser.parse_args()
    url = make_url(settings.migration_url)
    for checked in {url, make_url(settings.database_url)}:
        if reason := refusal(checked, settings.app_env):
            print(f"demo-reset refused: {reason}", file=sys.stderr)
            return 2
    print(f"Wiping {url.render_as_string(hide_password=True)} ...")
    asyncio.run(recreate(url))
    cfg = Config(str(HUB_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(HUB_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", settings.migration_url)
    command.upgrade(cfg, "head")

    from app import seed  # imports the engine only after the database exists again

    asyncio.run(seed.main(forecast=not args.no_forecast))
    return 0


if __name__ == "__main__":
    sys.exit(main())
