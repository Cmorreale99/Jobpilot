"""OAuth flow orchestration tests (mock provider, in-memory stores, no network)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.domain.credentials import PROVIDER_GDRIVE, PROVIDER_GITHUB, OAuthCredential
from app.integrations.oauth.base import OAuthError
from app.integrations.oauth.mock import MockOAuthProvider
from app.security.crypto import TokenCipher
from app.services.credentials import (
    InMemoryOAuthCredentialStore,
    resolve_drive_credentials,
)
from app.services.oauth_flow import InMemoryStateStore, OAuthFlowService


@pytest.fixture
def store() -> InMemoryOAuthCredentialStore:
    return InMemoryOAuthCredentialStore(TokenCipher(TokenCipher.generate_key()))


@pytest.fixture
def drive_provider() -> MockOAuthProvider:
    return MockOAuthProvider(PROVIDER_GDRIVE, account_label="jordan@example.com")


@pytest.fixture
def flow(
    store: InMemoryOAuthCredentialStore, drive_provider: MockOAuthProvider
) -> OAuthFlowService:
    return OAuthFlowService(
        store,
        {PROVIDER_GDRIVE: drive_provider, PROVIDER_GITHUB: MockOAuthProvider(PROVIDER_GITHUB)},
        InMemoryStateStore(),
    )


def test_start_returns_url_with_state(flow: OAuthFlowService) -> None:
    request = flow.start(PROVIDER_GDRIVE, "u1")
    assert request.state
    assert request.state in request.url
    assert request.url.startswith("https://mock-oauth.local/gdrive/authorize")


def test_start_unknown_provider_raises(flow: OAuthFlowService) -> None:
    with pytest.raises(OAuthError):
        flow.start("bitbucket", "u1")


def test_complete_stores_encrypted_credential(
    flow: OAuthFlowService, store: InMemoryOAuthCredentialStore
) -> None:
    request = flow.start(PROVIDER_GDRIVE, "u1")
    credential = flow.complete(request.state, "auth-code-xyz")

    assert credential.user_id == "u1"
    assert credential.provider == PROVIDER_GDRIVE
    assert credential.account_label == "jordan@example.com"
    assert credential.access_token == "gdrive-access-auth-code-xyz"
    # Persisted and retrievable (decrypted) from the store.
    assert store.get("u1", PROVIDER_GDRIVE) == credential
    # ... and the resolver hands it to the Drive client.
    assert resolve_drive_credentials(store, "u1").access_token == credential.access_token


def test_complete_invalid_state_raises(flow: OAuthFlowService) -> None:
    with pytest.raises(OAuthError):
        flow.complete("never-issued", "code")


def test_state_is_single_use(flow: OAuthFlowService) -> None:
    request = flow.start(PROVIDER_GDRIVE, "u1")
    flow.complete(request.state, "code")
    with pytest.raises(OAuthError):
        flow.complete(request.state, "code")  # replay rejected


def test_get_valid_credential_missing_returns_none(flow: OAuthFlowService) -> None:
    assert flow.get_valid_credential("nobody", PROVIDER_GDRIVE) is None


def test_get_valid_credential_not_expired_returns_same(
    flow: OAuthFlowService, store: InMemoryOAuthCredentialStore, drive_provider: MockOAuthProvider
) -> None:
    future = datetime.now(tz=UTC) + timedelta(hours=1)
    store.upsert(
        OAuthCredential("u1", PROVIDER_GDRIVE, "jordan@example.com", "tok", "refresh", (), future)
    )
    got = flow.get_valid_credential("u1", PROVIDER_GDRIVE)
    assert got is not None and got.access_token == "tok"
    assert drive_provider.refresh_calls == 0


def test_get_valid_credential_refreshes_when_expired(
    flow: OAuthFlowService, store: InMemoryOAuthCredentialStore, drive_provider: MockOAuthProvider
) -> None:
    past = datetime.now(tz=UTC) - timedelta(minutes=5)
    store.upsert(
        OAuthCredential("u1", PROVIDER_GDRIVE, "jordan@example.com", "old", "refresh", (), past)
    )
    got = flow.get_valid_credential("u1", PROVIDER_GDRIVE)
    assert got is not None
    assert drive_provider.refresh_calls == 1
    assert got.access_token == "gdrive-access-refreshed-1"
    # The refreshed token is persisted.
    assert store.get("u1", PROVIDER_GDRIVE).access_token == "gdrive-access-refreshed-1"  # type: ignore[union-attr]


def test_no_refresh_without_refresh_token(
    flow: OAuthFlowService, store: InMemoryOAuthCredentialStore, drive_provider: MockOAuthProvider
) -> None:
    past = datetime.now(tz=UTC) - timedelta(minutes=5)
    store.upsert(
        OAuthCredential("u1", PROVIDER_GDRIVE, "jordan@example.com", "tok", None, (), past)
    )
    got = flow.get_valid_credential("u1", PROVIDER_GDRIVE)
    assert got is not None and got.access_token == "tok"
    assert drive_provider.refresh_calls == 0  # can't refresh without a refresh token
