"""Google OAuth 2.0 authorization-code provider (for Google Drive access).

Endpoints are the standard Google OAuth/OIDC ones; they can be overridden via constructor
args for testing. HTTP goes through an injectable :class:`httpx.Client`, so request/response
mapping is unit-tested offline with ``httpx.MockTransport`` — no network, no real secrets.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx

from app.config import Settings
from app.domain.credentials import PROVIDER_GDRIVE
from app.integrations.oauth.base import OAuthError, OAuthToken

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


class GoogleOAuthProvider:
    """Google authorization-code flow, requesting offline access (a refresh token)."""

    name = PROVIDER_GDRIVE

    def __init__(
        self,
        settings: Settings,
        client: httpx.Client | None = None,
        *,
        auth_url: str = _AUTH_URL,
        token_url: str = _TOKEN_URL,
        userinfo_url: str = _USERINFO_URL,
    ) -> None:
        self._settings = settings
        self._client = client or httpx.Client(timeout=15.0)
        self._auth_url = auth_url
        self._token_url = token_url
        self._userinfo_url = userinfo_url

    def _require(self, *fields: str) -> None:
        missing = [f for f in fields if not getattr(self._settings, f)]
        if missing:
            raise OAuthError(f"Google OAuth is not configured; set: {', '.join(missing)}.")

    def authorization_url(self, state: str) -> str:
        self._require("google_oauth_client_id", "google_oauth_redirect_uri")
        params = {
            "client_id": self._settings.google_oauth_client_id,
            "redirect_uri": self._settings.google_oauth_redirect_uri,
            "response_type": "code",
            "scope": " ".join(self._settings.google_oauth_scope_list),
            "access_type": "offline",  # request a refresh token
            "prompt": "consent",
            "state": state,
        }
        return f"{self._auth_url}?{urlencode(params)}"

    def exchange_code(self, code: str) -> OAuthToken:
        self._require(
            "google_oauth_client_id",
            "google_oauth_client_secret",
            "google_oauth_redirect_uri",
        )
        data = self._post_token(
            {
                "code": code,
                "client_id": self._settings.google_oauth_client_id,
                "client_secret": self._settings.google_oauth_client_secret,
                "redirect_uri": self._settings.google_oauth_redirect_uri,
                "grant_type": "authorization_code",
            }
        )
        access_token = _require_access_token(data)
        return OAuthToken(
            access_token=access_token,
            account_label=self._fetch_email(access_token),
            refresh_token=_opt_str(data.get("refresh_token")),
            scopes=tuple(str(data.get("scope", "")).split()),
            expires_at=_expiry(data.get("expires_in")),
        )

    def refresh(self, refresh_token: str) -> OAuthToken:
        self._require("google_oauth_client_id", "google_oauth_client_secret")
        data = self._post_token(
            {
                "refresh_token": refresh_token,
                "client_id": self._settings.google_oauth_client_id,
                "client_secret": self._settings.google_oauth_client_secret,
                "grant_type": "refresh_token",
            }
        )
        access_token = _require_access_token(data)
        return OAuthToken(
            access_token=access_token,
            account_label=self._fetch_email(access_token),
            # Google usually omits a new refresh token on refresh; caller keeps the old one.
            refresh_token=_opt_str(data.get("refresh_token")),
            scopes=tuple(str(data.get("scope", "")).split()),
            expires_at=_expiry(data.get("expires_in")),
        )

    def _post_token(self, form: dict[str, str]) -> dict[str, object]:
        try:
            response = self._client.post(self._token_url, data=form)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OAuthError(f"Google token request failed: {exc}") from exc
        result: dict[str, object] = response.json()
        return result

    def _fetch_email(self, access_token: str) -> str:
        try:
            response = self._client.get(
                self._userinfo_url, headers={"Authorization": f"Bearer {access_token}"}
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OAuthError(f"Google userinfo request failed: {exc}") from exc
        email = response.json().get("email")
        if not email:
            raise OAuthError("Google userinfo did not return an email.")
        return str(email)


def _expiry(expires_in: object) -> datetime | None:
    if isinstance(expires_in, bool) or not isinstance(expires_in, (int, str)):
        return None
    try:
        seconds = int(expires_in)
    except ValueError:
        return None
    return datetime.now(tz=UTC) + timedelta(seconds=seconds)


def _require_access_token(data: dict[str, object]) -> str:
    token = data.get("access_token")
    if not token:
        raise OAuthError("Token response did not include an access_token.")
    return str(token)


def _opt_str(value: object) -> str | None:
    return str(value) if value else None
