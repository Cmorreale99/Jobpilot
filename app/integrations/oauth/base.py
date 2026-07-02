"""OAuth provider contract and the token value it returns.

An :class:`OAuthProvider` encapsulates one provider's authorization-code flow: build the
consent URL, exchange an authorization code for tokens, and refresh an access token. The
flow service (``app/services/oauth_flow.py``) drives these and persists results into the
encrypted credential store — the provider never touches storage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


class OAuthError(RuntimeError):
    """Raised on misconfiguration or a failed token exchange/refresh."""


@dataclass(frozen=True)
class OAuthToken:
    """Tokens returned by a provider after code exchange or refresh.

    ``account_label`` is the provider-facing identity resolved during exchange — a Google
    account email or a GitHub username — used as the credential's account label.
    """

    access_token: str
    account_label: str
    refresh_token: str | None = None
    scopes: tuple[str, ...] = ()
    expires_at: datetime | None = None


@runtime_checkable
class OAuthProvider(Protocol):
    """One provider's authorization-code flow."""

    name: str  # provider id, aligned with cv_sources.source_type (e.g. "gdrive", "github")

    def authorization_url(self, state: str) -> str:
        """Build the consent URL to redirect the user to, carrying an anti-CSRF ``state``."""
        ...

    def exchange_code(self, code: str) -> OAuthToken:
        """Exchange an authorization ``code`` for tokens (and resolve the account label)."""
        ...

    def refresh(self, refresh_token: str) -> OAuthToken:
        """Exchange a refresh token for a fresh access token."""
        ...
