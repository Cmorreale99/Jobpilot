"""GitHub OAuth authorization-code provider.

Classic OAuth App tokens do not expire and have no refresh token, so :meth:`refresh`
raises. HTTP goes through an injectable :class:`httpx.Client` for offline testing.
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx

from app.config import Settings
from app.domain.credentials import PROVIDER_GITHUB
from app.integrations.oauth.base import OAuthError, OAuthToken

_AUTH_URL = "https://github.com/login/oauth/authorize"
_TOKEN_URL = "https://github.com/login/oauth/access_token"
_USER_URL = "https://api.github.com/user"


class GitHubOAuthProvider:
    """GitHub authorization-code flow."""

    name = PROVIDER_GITHUB

    def __init__(
        self,
        settings: Settings,
        client: httpx.Client | None = None,
        *,
        auth_url: str = _AUTH_URL,
        token_url: str = _TOKEN_URL,
        user_url: str = _USER_URL,
    ) -> None:
        self._settings = settings
        self._client = client or httpx.Client(timeout=15.0)
        self._auth_url = auth_url
        self._token_url = token_url
        self._user_url = user_url

    def _require(self, *fields: str) -> None:
        missing = [f for f in fields if not getattr(self._settings, f)]
        if missing:
            raise OAuthError(f"GitHub OAuth is not configured; set: {', '.join(missing)}.")

    def authorization_url(self, state: str) -> str:
        self._require("github_oauth_client_id", "github_oauth_redirect_uri")
        params = {
            "client_id": self._settings.github_oauth_client_id,
            "redirect_uri": self._settings.github_oauth_redirect_uri,
            "scope": " ".join(self._settings.github_oauth_scope_list),
            "state": state,
        }
        return f"{self._auth_url}?{urlencode(params)}"

    def exchange_code(self, code: str) -> OAuthToken:
        self._require(
            "github_oauth_client_id",
            "github_oauth_client_secret",
            "github_oauth_redirect_uri",
        )
        try:
            response = self._client.post(
                self._token_url,
                data={
                    "client_id": self._settings.github_oauth_client_id,
                    "client_secret": self._settings.github_oauth_client_secret,
                    "code": code,
                    "redirect_uri": self._settings.github_oauth_redirect_uri,
                },
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OAuthError(f"GitHub token request failed: {exc}") from exc
        data = response.json()
        if data.get("error"):
            raise OAuthError(f"GitHub token exchange error: {data.get('error_description', data)}")
        access_token = data.get("access_token")
        if not access_token:
            raise OAuthError("GitHub token response did not include an access_token.")
        # GitHub scopes come back comma-separated.
        scopes = tuple(s.strip() for s in str(data.get("scope", "")).split(",") if s.strip())
        return OAuthToken(
            access_token=str(access_token),
            account_label=self._fetch_login(str(access_token)),
            refresh_token=None,
            scopes=scopes,
            expires_at=None,
        )

    def refresh(self, refresh_token: str) -> OAuthToken:
        raise OAuthError(
            "GitHub OAuth App tokens do not expire and cannot be refreshed. "
            "Re-run the authorization flow to obtain a new token."
        )

    def _fetch_login(self, access_token: str) -> str:
        try:
            response = self._client.get(
                self._user_url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OAuthError(f"GitHub user request failed: {exc}") from exc
        login = response.json().get("login")
        if not login:
            raise OAuthError("GitHub user response did not return a login.")
        return str(login)
