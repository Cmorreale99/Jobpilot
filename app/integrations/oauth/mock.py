"""In-memory mock OAuth provider for offline tests and the mock-first pipeline.

Performs no network I/O: it returns deterministic tokens so the full authorization flow
(start → callback → store → refresh) can be exercised without real client secrets.
"""

from __future__ import annotations

from datetime import datetime

from app.integrations.oauth.base import OAuthToken


class MockOAuthProvider:
    """A provider that fabricates tokens deterministically.

    Args:
        name: provider id (e.g. ``"gdrive"``/``"github"``).
        account_label: the identity returned from ``exchange_code``.
        refresh_token: refresh token to hand back (``None`` to simulate non-refreshable).
        expires_at: expiry stamped on issued tokens (``None`` for non-expiring).
    """

    def __init__(
        self,
        name: str,
        account_label: str = "mock-user@example.com",
        *,
        refresh_token: str | None = "mock-refresh-token",
        expires_at: datetime | None = None,
    ) -> None:
        self.name = name
        self._account_label = account_label
        self._refresh_token = refresh_token
        self._expires_at = expires_at
        self.refresh_calls = 0

    def authorization_url(self, state: str) -> str:
        return f"https://mock-oauth.local/{self.name}/authorize?state={state}"

    def exchange_code(self, code: str) -> OAuthToken:
        return OAuthToken(
            access_token=f"{self.name}-access-{code}",
            account_label=self._account_label,
            refresh_token=self._refresh_token,
            scopes=("mock.read",),
            expires_at=self._expires_at,
        )

    def refresh(self, refresh_token: str) -> OAuthToken:
        self.refresh_calls += 1
        return OAuthToken(
            access_token=f"{self.name}-access-refreshed-{self.refresh_calls}",
            account_label=self._account_label,
            refresh_token=self._refresh_token,
            scopes=("mock.read",),
            expires_at=self._expires_at,
        )
