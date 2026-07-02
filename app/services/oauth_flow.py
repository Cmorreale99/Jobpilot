"""OAuth authorization-flow orchestration.

Ties providers, the anti-CSRF state store, and the encrypted credential store together:

* :meth:`OAuthFlowService.start` — begin authorization: mint a ``state``, persist the
  pending ``(provider, user)``, and return the provider's consent URL.
* :meth:`OAuthFlowService.complete` — handle the callback: validate ``state``, exchange
  the code for tokens, and upsert an encrypted :class:`OAuthCredential`.
* :meth:`OAuthFlowService.get_valid_credential` — return a stored credential, transparently
  refreshing it when it is expired (or about to expire) and a refresh token exists.

Everything is offline-testable: inject a :class:`MockOAuthProvider` and the in-memory
stores. No network or real secrets are needed to exercise the full flow.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.domain.credentials import OAuthCredential, OAuthCredentialStore
from app.integrations.oauth.base import OAuthError, OAuthProvider, OAuthToken


@dataclass(frozen=True)
class AuthorizationRequest:
    """The consent URL to redirect the user to, plus the ``state`` that guards it."""

    url: str
    state: str


class OAuthStateStore(Protocol):
    """Stores pending ``(provider, user_id)`` keyed by an opaque ``state`` token."""

    def put(self, state: str, provider: str, user_id: str) -> None: ...

    def pop(self, state: str) -> tuple[str, str] | None:
        """Return and consume ``(provider, user_id)`` for ``state`` (single-use)."""
        ...


class InMemoryStateStore:
    """Non-persistent, single-use state store for the authorization handshake."""

    def __init__(self) -> None:
        self._pending: dict[str, tuple[str, str]] = {}

    def put(self, state: str, provider: str, user_id: str) -> None:
        self._pending[state] = (provider, user_id)

    def pop(self, state: str) -> tuple[str, str] | None:
        return self._pending.pop(state, None)


class OAuthFlowService:
    """Drives the authorization-code flow and persists encrypted credentials."""

    def __init__(
        self,
        store: OAuthCredentialStore,
        providers: Mapping[str, OAuthProvider],
        state_store: OAuthStateStore | None = None,
        *,
        refresh_skew: timedelta = timedelta(seconds=60),
    ) -> None:
        self._store = store
        self._providers = dict(providers)
        self._state_store = state_store or InMemoryStateStore()
        self._refresh_skew = refresh_skew

    def _provider(self, provider: str) -> OAuthProvider:
        try:
            return self._providers[provider]
        except KeyError as exc:
            raise OAuthError(f"No OAuth provider registered for {provider!r}.") from exc

    def start(self, provider: str, user_id: str) -> AuthorizationRequest:
        """Begin authorization for ``(provider, user_id)``."""
        adapter = self._provider(provider)
        state = secrets.token_urlsafe(32)
        self._state_store.put(state, provider, user_id)
        return AuthorizationRequest(url=adapter.authorization_url(state), state=state)

    def complete(self, state: str, code: str) -> OAuthCredential:
        """Handle the callback: validate ``state``, exchange ``code``, store credentials."""
        pending = self._state_store.pop(state)
        if pending is None:
            raise OAuthError("Invalid or expired OAuth state.")
        provider, user_id = pending
        token = self._provider(provider).exchange_code(code)
        credential = _to_credential(user_id, provider, token)
        self._store.upsert(credential)
        return credential

    def get_valid_credential(
        self, user_id: str, provider: str, *, now: datetime | None = None
    ) -> OAuthCredential | None:
        """Return the stored credential, refreshing it if it is expired/near-expiry."""
        credential = self._store.get(user_id, provider)
        if credential is None:
            return None
        if not self._needs_refresh(credential, now or datetime.now(tz=UTC)):
            return credential

        token = self._provider(provider).refresh(credential.refresh_token or "")
        refreshed = OAuthCredential(
            user_id=user_id,
            provider=provider,
            account_label=token.account_label or credential.account_label,
            access_token=token.access_token,
            # Providers often omit a new refresh token; keep the existing one.
            refresh_token=token.refresh_token or credential.refresh_token,
            scopes=token.scopes or credential.scopes,
            expires_at=token.expires_at,
        )
        self._store.upsert(refreshed)
        return refreshed

    def _needs_refresh(self, credential: OAuthCredential, now: datetime) -> bool:
        if credential.expires_at is None or credential.refresh_token is None:
            return False
        return credential.expires_at <= now + self._refresh_skew


def _to_credential(user_id: str, provider: str, token: OAuthToken) -> OAuthCredential:
    return OAuthCredential(
        user_id=user_id,
        provider=provider,
        account_label=token.account_label,
        access_token=token.access_token,
        refresh_token=token.refresh_token,
        scopes=token.scopes,
        expires_at=token.expires_at,
    )
