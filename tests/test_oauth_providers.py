"""Offline tests for real Google/GitHub providers using httpx.MockTransport."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
from app.config import Settings
from app.integrations.oauth.base import OAuthError
from app.integrations.oauth.factory import create_oauth_providers
from app.integrations.oauth.github import GitHubOAuthProvider
from app.integrations.oauth.google import GoogleOAuthProvider

Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _google_settings() -> Settings:
    return Settings(
        google_oauth_client_id="gid",
        google_oauth_client_secret="gsecret",
        google_oauth_redirect_uri="https://app.local/oauth/google/callback",
    )


def _github_settings() -> Settings:
    return Settings(
        github_oauth_client_id="ghid",
        github_oauth_client_secret="ghsecret",
        github_oauth_redirect_uri="https://app.local/oauth/github/callback",
    )


# --- Google ----------------------------------------------------------------------


def test_google_authorization_url_has_offline_access() -> None:
    url = GoogleOAuthProvider(_google_settings()).authorization_url("st8")
    assert "client_id=gid" in url
    assert "access_type=offline" in url
    assert "state=st8" in url
    assert "drive.readonly" in url


def test_google_exchange_code_maps_token_and_email() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(
                200,
                json={
                    "access_token": "ga-tok",
                    "refresh_token": "ga-refresh",
                    "expires_in": 3599,
                    "scope": "openid email https://www.googleapis.com/auth/drive.readonly",
                },
            )
        if "userinfo" in request.url.path:
            return httpx.Response(200, json={"email": "jordan@example.com"})
        return httpx.Response(404)

    provider = GoogleOAuthProvider(_google_settings(), _client(handler))
    token = provider.exchange_code("code-123")

    assert token.access_token == "ga-tok"
    assert token.account_label == "jordan@example.com"
    assert token.refresh_token == "ga-refresh"
    assert "https://www.googleapis.com/auth/drive.readonly" in token.scopes
    assert token.expires_at is not None


def test_google_refresh_returns_new_access_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "ga-tok-2", "expires_in": 3599})
        return httpx.Response(200, json={"email": "jordan@example.com"})

    token = GoogleOAuthProvider(_google_settings(), _client(handler)).refresh("ga-refresh")
    assert token.access_token == "ga-tok-2"


def test_google_token_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    with pytest.raises(OAuthError):
        GoogleOAuthProvider(_google_settings(), _client(handler)).exchange_code("bad")


def test_google_missing_config_raises() -> None:
    with pytest.raises(OAuthError):
        GoogleOAuthProvider(Settings()).authorization_url("s")


# --- GitHub ----------------------------------------------------------------------


def test_github_authorization_url() -> None:
    url = GitHubOAuthProvider(_github_settings()).authorization_url("st8")
    assert "client_id=ghid" in url
    assert "state=st8" in url


def test_github_exchange_code_maps_token_and_login() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_token"):
            return httpx.Response(200, json={"access_token": "gh-tok", "scope": "read:user,repo"})
        if request.url.path.endswith("/user"):
            return httpx.Response(200, json={"login": "jordanrivera"})
        return httpx.Response(404)

    token = GitHubOAuthProvider(_github_settings(), _client(handler)).exchange_code("code")

    assert token.access_token == "gh-tok"
    assert token.account_label == "jordanrivera"
    assert token.scopes == ("read:user", "repo")
    assert token.refresh_token is None
    assert token.expires_at is None


def test_github_exchange_error_body_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "bad_verification_code"})

    with pytest.raises(OAuthError):
        GitHubOAuthProvider(_github_settings(), _client(handler)).exchange_code("bad")


def test_github_refresh_not_supported() -> None:
    with pytest.raises(OAuthError):
        GitHubOAuthProvider(_github_settings()).refresh("whatever")


# --- factory ---------------------------------------------------------------------


def test_factory_builds_both_providers() -> None:
    providers = create_oauth_providers(_google_settings())
    assert set(providers) == {"gdrive", "github"}
    assert providers["gdrive"].name == "gdrive"
    assert providers["github"].name == "github"
