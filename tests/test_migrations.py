"""Verify the Alembic migration builds the oauth_credentials schema (offline SQLite)."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from app.db.credentials_store import SqlOAuthCredentialStore
from app.db.session import create_session_factory
from app.domain.credentials import PROVIDER_GDRIVE, OAuthCredential
from app.security.crypto import TokenCipher

EXPECTED_COLUMNS = {
    "id",
    "user_id",
    "provider",
    "account_label",
    "encrypted_access_token",
    "encrypted_refresh_token",
    "scopes",
    "expires_at",
    "updated_at",
}


def _config(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _sqlite_url(tmp_path: Path, name: str) -> str:
    return f"sqlite+pysqlite:///{tmp_path / name}"


def test_upgrade_creates_schema_then_downgrade_drops_it(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path, "up.db")
    cfg = _config(url)

    command.upgrade(cfg, "head")
    inspector = sa.inspect(sa.create_engine(url))
    assert "oauth_credentials" in inspector.get_table_names()
    columns = {c["name"] for c in inspector.get_columns("oauth_credentials")}
    assert columns >= EXPECTED_COLUMNS
    uniques = {u["name"] for u in inspector.get_unique_constraints("oauth_credentials")}
    assert "uq_oauth_user_provider" in uniques
    indexes = {i["name"] for i in inspector.get_indexes("oauth_credentials")}
    assert "ix_oauth_credentials_user_id" in indexes

    command.downgrade(cfg, "base")
    assert "oauth_credentials" not in sa.inspect(sa.create_engine(url)).get_table_names()


def test_upgrade_creates_master_cv_and_cv_sources(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path, "cv.db")
    command.upgrade(_config(url), "head")
    tables = set(sa.inspect(sa.create_engine(url)).get_table_names())
    assert {"master_cv", "cv_sources"} <= tables


def test_sql_store_round_trips_on_migrated_schema(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path, "store.db")
    command.upgrade(_config(url), "head")

    store = SqlOAuthCredentialStore(
        create_session_factory(sa.create_engine(url)),
        TokenCipher(TokenCipher.generate_key()),
    )
    store.upsert(OAuthCredential("u1", PROVIDER_GDRIVE, "jordan@example.com", "tok"))
    got = store.get("u1", PROVIDER_GDRIVE)
    assert got is not None and got.access_token == "tok"
