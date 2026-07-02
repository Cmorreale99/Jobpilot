"""Engine and session factory construction.

The pipeline is DB-agnostic via ``DATABASE_URL`` — Postgres in production, SQLite for
tests/offline. ``create_all`` is a convenience for SQLite/tests; production schema is
managed by migrations (TODO: add Alembic — see PLAN.md).
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db.base import Base


def create_db_engine(settings: Settings | None = None) -> Engine:
    settings = settings or get_settings()
    if not settings.database_url:
        raise ValueError("DATABASE_URL is not set.")
    return create_engine(settings.database_url)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def create_all(engine: Engine) -> None:
    """Create all tables. For SQLite/tests; production uses migrations."""
    Base.metadata.create_all(engine)
