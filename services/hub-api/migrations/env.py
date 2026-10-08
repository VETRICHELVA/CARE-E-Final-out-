import asyncio
from logging.config import fileConfig

import app.audit.models
import app.auth.models
import app.catalog.models
import app.events.models
import app.forecasting.models
import app.inventory.models
import app.iot.models
import app.notifications.models
import app.orgs.models
import app.purchase_orders.models
import app.receiving.models
import app.recommendations.models
import app.shipments.models
import app.shortages.models
import app.source_requests.models
import app.surplus.models  # noqa: F401  (register every model on Base.metadata)
from alembic import context
from app.config import settings
from app.db import Base
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
url = config.get_main_option("sqlalchemy.url") or settings.database_url


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(url, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(run_async_migrations())
