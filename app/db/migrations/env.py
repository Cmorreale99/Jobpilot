"""Alembic migration environment.

The database URL is resolved from application settings (``DATABASE_URL``) unless the
Alembic config supplies one explicitly (tests point it at a temp SQLite file). Target
metadata is ``Base.metadata`` with all models imported so autogenerate sees every table.
"""

from __future__ import annotations

from logging.config import fileConfig

# Import models so their tables register on Base.metadata.
import app.db.models  # noqa: F401
from alembic import context
from app.config import get_settings
from app.db.base import Base
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    # Keep app loggers alive when Alembic runs programmatically (tests, dev bootstrap):
    # fileConfig's default disable_existing_loggers=True would silence them.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
