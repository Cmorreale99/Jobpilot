"""SQLAlchemy-backed credential store, exercised against in-memory SQLite."""

from __future__ import annotations

import pytest
from app.db.credentials_store import SqlOAuthCredentialStore
from app.db.models import OAuthCredentialRow
from app.db.session import create_all, create_session_factory
from app.domain.credentials import PROVIDER_GDRIVE, PROVIDER_GITHUB, OAuthCredential
from app.security.crypto import TokenCipher
from sqlalchemy import Engine, create_engine, text


@pytest.fixture
def engine() -> Engine:
    eng = create_engine("sqlite+pysqlite:///:memory:")
    create_all(eng)
    return eng


@pytest.fixture
def store(engine: Engine) -> SqlOAuthCredentialStore:
    return SqlOAuthCredentialStore(
        create_session_factory(engine), TokenCipher(TokenCipher.generate_key())
    )


def test_upsert_and_get_round_trip(store: SqlOAuthCredentialStore) -> None:
    cred = OAuthCredential(
        user_id="u1",
        provider=PROVIDER_GDRIVE,
        account_label="jordan@example.com",
        access_token="drive-token",
        refresh_token="refresh",
        scopes=("drive.readonly", "drive.metadata.readonly"),
    )
    store.upsert(cred)
    assert store.get("u1", PROVIDER_GDRIVE) == cred


def test_column_holds_ciphertext_not_plaintext(
    engine: Engine, store: SqlOAuthCredentialStore
) -> None:
    store.upsert(OAuthCredential("u1", PROVIDER_GITHUB, "jordanrivera", "ghp_secret_xyz"))
    with engine.connect() as conn:
        stored = conn.execute(
            text("SELECT encrypted_access_token FROM oauth_credentials")
        ).scalar_one()
    assert stored != "ghp_secret_xyz"
    assert "ghp_secret_xyz" not in str(stored)


def test_upsert_updates_existing_row(store: SqlOAuthCredentialStore, engine: Engine) -> None:
    store.upsert(OAuthCredential("u1", PROVIDER_GITHUB, "old", "t1"))
    store.upsert(OAuthCredential("u1", PROVIDER_GITHUB, "new", "t2"))
    got = store.get("u1", PROVIDER_GITHUB)
    assert got is not None and got.account_label == "new" and got.access_token == "t2"
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM oauth_credentials")).scalar_one()
    assert count == 1  # updated, not duplicated


def test_delete(store: SqlOAuthCredentialStore) -> None:
    store.upsert(OAuthCredential("u1", PROVIDER_GDRIVE, "a@b.com", "t"))
    store.delete("u1", PROVIDER_GDRIVE)
    assert store.get("u1", PROVIDER_GDRIVE) is None


def test_get_missing_returns_none(store: SqlOAuthCredentialStore) -> None:
    assert store.get("nobody", PROVIDER_GDRIVE) is None


def test_model_table_name() -> None:
    assert OAuthCredentialRow.__tablename__ == "oauth_credentials"


def test_expires_at_survives_sqlite_round_trip_timezone_aware() -> None:
    """SQLite strips tzinfo from DateTime columns; the store must re-tag UTC so the
    token-refresh comparison never mixes naive and aware datetimes (seen live)."""
    from datetime import UTC, datetime, timedelta

    import sqlalchemy as sa
    from app.db.credentials_store import SqlOAuthCredentialStore
    from app.db.session import create_all, create_session_factory
    from app.domain.credentials import PROVIDER_GDRIVE, OAuthCredential
    from app.integrations.oauth.mock import MockOAuthProvider
    from app.security.crypto import TokenCipher
    from app.services.oauth_flow import InMemoryStateStore, OAuthFlowService

    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    create_all(engine)
    store = SqlOAuthCredentialStore(
        create_session_factory(engine), TokenCipher(TokenCipher.generate_key())
    )
    store.upsert(
        OAuthCredential(
            user_id="u1",
            provider=PROVIDER_GDRIVE,
            account_label="jordan@example.com",
            access_token="tok",
            refresh_token="refresh-me",
            expires_at=datetime.now(tz=UTC) - timedelta(minutes=5),
        )
    )

    credential = store.get("u1", PROVIDER_GDRIVE)
    assert credential is not None
    assert credential.expires_at is not None and credential.expires_at.tzinfo is not None

    # The refresh path that crashed live: must renew, not raise.
    flow = OAuthFlowService(
        store, {PROVIDER_GDRIVE: MockOAuthProvider(PROVIDER_GDRIVE)}, InMemoryStateStore()
    )
    refreshed = flow.get_valid_credential("u1", PROVIDER_GDRIVE)
    assert refreshed is not None and refreshed.access_token != "tok"
