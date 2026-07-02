"""HTTP tests for the FastAPI OAuth routes (mock-backed flow, no network)."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from app.config import Settings
from app.domain.credentials import PROVIDER_GDRIVE, PROVIDER_GITHUB
from app.integrations.oauth.mock import MockOAuthProvider
from app.main import create_app
from app.security.crypto import TokenCipher
from app.services.credentials import InMemoryOAuthCredentialStore
from app.services.oauth_flow import InMemoryStateStore, OAuthFlowService
from fastapi.testclient import TestClient


def _flow_and_store() -> tuple[OAuthFlowService, InMemoryOAuthCredentialStore]:
    store = InMemoryOAuthCredentialStore(TokenCipher(TokenCipher.generate_key()))
    flow = OAuthFlowService(
        store,
        {
            PROVIDER_GDRIVE: MockOAuthProvider(PROVIDER_GDRIVE, "jordan@example.com"),
            PROVIDER_GITHUB: MockOAuthProvider(PROVIDER_GITHUB, "jordanrivera"),
        },
        InMemoryStateStore(),
    )
    return flow, store


def _state_from_location(location: str) -> str:
    return parse_qs(urlparse(location).query)["state"][0]


def test_health() -> None:
    client = TestClient(create_app(flow=_flow_and_store()[0]))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_start_redirects_to_consent() -> None:
    client = TestClient(create_app(flow=_flow_and_store()[0]))
    response = client.get("/oauth/gdrive/start", params={"user_id": "u1"}, follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://mock-oauth.local/gdrive/authorize")
    assert "state=" in location


def test_start_then_callback_stores_credential() -> None:
    flow, store = _flow_and_store()
    client = TestClient(create_app(flow=flow))

    started = client.get("/oauth/gdrive/start", params={"user_id": "u1"}, follow_redirects=False)
    state = _state_from_location(started.headers["location"])

    callback = client.get("/oauth/gdrive/callback", params={"code": "auth-code", "state": state})
    assert callback.status_code == 200
    body = callback.json()
    assert body["status"] == "connected"
    assert body["provider"] == "gdrive"
    assert body["account_label"] == "jordan@example.com"
    # The credential is persisted (and decryptable) in the store.
    stored = store.get("u1", PROVIDER_GDRIVE)
    assert stored is not None and stored.access_token == "gdrive-access-auth-code"


def test_callback_invalid_state_is_rejected() -> None:
    client = TestClient(create_app(flow=_flow_and_store()[0]))
    response = client.get("/oauth/gdrive/callback", params={"code": "x", "state": "never-issued"})
    assert response.status_code == 400


def test_callback_provider_error_is_rejected() -> None:
    client = TestClient(create_app(flow=_flow_and_store()[0]))
    response = client.get("/oauth/gdrive/callback", params={"error": "access_denied", "state": "s"})
    assert response.status_code == 400


def test_callback_provider_mismatch_is_rejected() -> None:
    flow, _ = _flow_and_store()
    client = TestClient(create_app(flow=flow))
    started = client.get("/oauth/gdrive/start", params={"user_id": "u1"}, follow_redirects=False)
    state = _state_from_location(started.headers["location"])
    # Use the gdrive-issued state on the github callback path.
    response = client.get("/oauth/github/callback", params={"code": "c", "state": state})
    assert response.status_code == 400


def test_start_unknown_provider_is_rejected() -> None:
    client = TestClient(create_app(flow=_flow_and_store()[0]))
    response = client.get("/oauth/bitbucket/start", params={"user_id": "u1"})
    assert response.status_code == 400


def test_missing_config_returns_503() -> None:
    # No injected flow and no encryption key -> lazy build fails with a clear 503.
    app = create_app(settings=Settings(token_encryption_key=""))
    response = TestClient(app).get("/oauth/gdrive/start", params={"user_id": "u1"})
    assert response.status_code == 503
