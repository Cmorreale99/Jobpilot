"""In-memory credential store, resolvers, and factory credential wiring."""

from __future__ import annotations

import pytest
from app.config import Settings
from app.domain.credentials import (
    PROVIDER_GDRIVE,
    PROVIDER_GITHUB,
    OAuthCredential,
    OAuthCredentialStore,
)
from app.integrations.base import DriveConfigurationError, GitHubConfigurationError
from app.integrations.drive_factory import create_drive_client
from app.integrations.github_factory import create_github_client
from app.integrations.mcp.drive import McpDriveClient
from app.integrations.mcp.github import McpGitHubClient
from app.security.crypto import TokenCipher
from app.services.credentials import (
    InMemoryOAuthCredentialStore,
    resolve_drive_credentials,
    resolve_github_credentials,
)


@pytest.fixture
def store() -> InMemoryOAuthCredentialStore:
    return InMemoryOAuthCredentialStore(TokenCipher(TokenCipher.generate_key()))


def test_store_satisfies_protocol(store: InMemoryOAuthCredentialStore) -> None:
    assert isinstance(store, OAuthCredentialStore)


def test_upsert_and_get_round_trip(store: InMemoryOAuthCredentialStore) -> None:
    cred = OAuthCredential(
        user_id="u1",
        provider=PROVIDER_GDRIVE,
        account_label="jordan@example.com",
        access_token="drive-token",
        refresh_token="drive-refresh",
        scopes=("drive.readonly",),
    )
    store.upsert(cred)
    got = store.get("u1", PROVIDER_GDRIVE)
    assert got == cred


def test_tokens_encrypted_at_rest(store: InMemoryOAuthCredentialStore) -> None:
    store.upsert(OAuthCredential("u1", PROVIDER_GITHUB, "jordanrivera", "ghp_plaintext_secret"))
    # The internal row holds ciphertext, not the plaintext token.
    row = store._rows[("u1", PROVIDER_GITHUB)]  # noqa: SLF001 - asserting at-rest encryption
    assert row["access_token"] != "ghp_plaintext_secret"
    assert "ghp_plaintext_secret" not in str(row["access_token"])


def test_upsert_replaces_existing(store: InMemoryOAuthCredentialStore) -> None:
    store.upsert(OAuthCredential("u1", PROVIDER_GITHUB, "old", "t1"))
    store.upsert(OAuthCredential("u1", PROVIDER_GITHUB, "new", "t2"))
    got = store.get("u1", PROVIDER_GITHUB)
    assert got is not None and got.account_label == "new" and got.access_token == "t2"


def test_delete(store: InMemoryOAuthCredentialStore) -> None:
    store.upsert(OAuthCredential("u1", PROVIDER_GDRIVE, "a@b.com", "t"))
    store.delete("u1", PROVIDER_GDRIVE)
    assert store.get("u1", PROVIDER_GDRIVE) is None


def test_get_missing_returns_none(store: InMemoryOAuthCredentialStore) -> None:
    assert store.get("nobody", PROVIDER_GDRIVE) is None


# --- resolvers -------------------------------------------------------------------


def test_resolve_drive_credentials(store: InMemoryOAuthCredentialStore) -> None:
    store.upsert(OAuthCredential("u1", PROVIDER_GDRIVE, "jordan@example.com", "d-tok"))
    creds = resolve_drive_credentials(store, "u1")
    assert creds.user_email == "jordan@example.com"
    assert creds.access_token == "d-tok"


def test_resolve_github_credentials(store: InMemoryOAuthCredentialStore) -> None:
    store.upsert(OAuthCredential("u1", PROVIDER_GITHUB, "jordanrivera", "g-tok"))
    assert resolve_github_credentials(store, "u1").access_token == "g-tok"


def test_resolve_missing_drive_raises(store: InMemoryOAuthCredentialStore) -> None:
    with pytest.raises(DriveConfigurationError):
        resolve_drive_credentials(store, "u1")


def test_resolve_missing_github_raises(store: InMemoryOAuthCredentialStore) -> None:
    with pytest.raises(GitHubConfigurationError):
        resolve_github_credentials(store, "u1")


# --- factory wiring --------------------------------------------------------------


def _drive_mcp_settings() -> Settings:
    return Settings(
        gdrive_mcp_enabled=True,
        gdrive_mcp_server="http://localhost:9000/mcp",
        gdrive_source_folder_id="career_docs",
    )


def _github_mcp_settings() -> Settings:
    return Settings(
        github_mcp_enabled=True,
        github_mcp_server="http://localhost:9100/mcp",
        github_username="jordanrivera",
    )


def test_drive_factory_injects_resolved_credentials(
    store: InMemoryOAuthCredentialStore,
) -> None:
    store.upsert(OAuthCredential("u1", PROVIDER_GDRIVE, "jordan@example.com", "d-tok"))
    client = create_drive_client(_drive_mcp_settings(), store=store, user_id="u1")
    assert isinstance(client, McpDriveClient)
    # Injected credentials mean a call passes the credential gate (fails later on transport).
    assert client._require_credentials().access_token == "d-tok"  # noqa: SLF001


def test_github_factory_injects_resolved_credentials(
    store: InMemoryOAuthCredentialStore,
) -> None:
    store.upsert(OAuthCredential("u1", PROVIDER_GITHUB, "jordanrivera", "g-tok"))
    client = create_github_client(_github_mcp_settings(), store=store, user_id="u1")
    assert isinstance(client, McpGitHubClient)
    assert client._require_credentials().access_token == "g-tok"  # noqa: SLF001


def test_drive_factory_without_store_leaves_credentials_unset() -> None:
    client = create_drive_client(_drive_mcp_settings())
    assert isinstance(client, McpDriveClient)
    with pytest.raises(DriveConfigurationError):
        client._require_credentials()  # noqa: SLF001


# --- refreshing read-through view (keeps nightly-job tokens fresh) -----------------


def test_refreshing_store_renews_near_expiry_tokens() -> None:
    from datetime import UTC, datetime, timedelta

    from app.domain.credentials import PROVIDER_GDRIVE, OAuthCredential
    from app.integrations.oauth.mock import MockOAuthProvider
    from app.security.crypto import TokenCipher
    from app.services.credentials import InMemoryOAuthCredentialStore, RefreshingCredentialStore
    from app.services.oauth_flow import InMemoryStateStore, OAuthFlowService

    store = InMemoryOAuthCredentialStore(TokenCipher(TokenCipher.generate_key()))
    store.upsert(
        OAuthCredential(
            user_id="u1",
            provider=PROVIDER_GDRIVE,
            account_label="jordan@example.com",
            access_token="stale-token",
            refresh_token="refresh-me",
            scopes=("https://www.googleapis.com/auth/gmail.send",),
            expires_at=datetime.now(tz=UTC) - timedelta(minutes=5),  # already expired
        )
    )
    provider = MockOAuthProvider(PROVIDER_GDRIVE, account_label="jordan@example.com")
    flow = OAuthFlowService(store, {PROVIDER_GDRIVE: provider}, InMemoryStateStore())
    refreshing = RefreshingCredentialStore(store, flow)

    credential = refreshing.get("u1", PROVIDER_GDRIVE)

    assert credential is not None
    assert credential.access_token != "stale-token"  # renewed via the provider
    assert provider.refresh_calls == 1
    # The renewal was persisted, so the next raw read sees the fresh token too.
    raw = store.get("u1", PROVIDER_GDRIVE)
    assert raw is not None and raw.access_token == credential.access_token
