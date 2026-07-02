"""Credential resolution and the in-memory (mock-first) credential store.

* :class:`InMemoryOAuthCredentialStore` — a fixture-friendly store that still encrypts
  tokens at rest (so tests exercise the crypto path), used when no database is wired.
* ``resolve_*_credentials`` — map a stored :class:`OAuthCredential` onto the
  integration-specific credential type each MCP client needs.

The resolvers fail with the client's own configuration error when a credential is
missing, so an unconfigured provider is reported clearly rather than silently skipped.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.credentials import (
    PROVIDER_GDRIVE,
    PROVIDER_GITHUB,
    OAuthCredential,
    OAuthCredentialStore,
)

if TYPE_CHECKING:  # pragma: no cover - typing only (avoids an import cycle)
    from app.config import Settings
    from app.services.oauth_flow import OAuthFlowService
from app.integrations.base import (
    DriveConfigurationError,
    DriveCredentials,
    GitHubConfigurationError,
    GitHubCredentials,
    MailConfigurationError,
    MailCredentials,
)
from app.security.crypto import TokenCipher


class InMemoryOAuthCredentialStore:
    """Non-persistent :class:`OAuthCredentialStore`, encrypting tokens at rest."""

    def __init__(self, cipher: TokenCipher) -> None:
        self._cipher = cipher
        # Values hold ciphertext, never plaintext tokens.
        self._rows: dict[tuple[str, str], dict[str, object]] = {}

    def upsert(self, credential: OAuthCredential) -> None:
        self._rows[(credential.user_id, credential.provider)] = {
            "account_label": credential.account_label,
            "access_token": self._cipher.encrypt(credential.access_token),
            "refresh_token": (
                self._cipher.encrypt(credential.refresh_token) if credential.refresh_token else None
            ),
            "scopes": " ".join(credential.scopes),
            "expires_at": credential.expires_at,
        }

    def get(self, user_id: str, provider: str) -> OAuthCredential | None:
        row = self._rows.get((user_id, provider))
        if row is None:
            return None
        refresh = row["refresh_token"]
        scopes = str(row["scopes"])
        return OAuthCredential(
            user_id=user_id,
            provider=provider,
            account_label=str(row["account_label"]),
            access_token=self._cipher.decrypt(str(row["access_token"])),
            refresh_token=self._cipher.decrypt(str(refresh)) if refresh else None,
            scopes=tuple(scopes.split()) if scopes else (),
            expires_at=row["expires_at"],  # type: ignore[arg-type]
        )

    def delete(self, user_id: str, provider: str) -> None:
        self._rows.pop((user_id, provider), None)


class RefreshingCredentialStore:
    """A read-through :class:`OAuthCredentialStore` view that keeps tokens fresh.

    ``get`` delegates to :meth:`OAuthFlowService.get_valid_credential`, which refreshes
    a near-expiry token (persisting the result) before returning it. Long-lived
    processes — the nightly jobs, the API — resolve credentials through this so a
    token minted hours ago never reaches a client stale. Writes pass through.
    """

    def __init__(self, store: OAuthCredentialStore, flow: OAuthFlowService) -> None:
        self._store = store
        self._flow = flow

    def get(self, user_id: str, provider: str) -> OAuthCredential | None:
        return self._flow.get_valid_credential(user_id, provider)

    def upsert(self, credential: OAuthCredential) -> None:
        self._store.upsert(credential)

    def delete(self, user_id: str, provider: str) -> None:
        self._store.delete(user_id, provider)


def create_refreshing_store(
    store: OAuthCredentialStore, settings: Settings
) -> OAuthCredentialStore:
    """Wrap ``store`` so reads auto-refresh, using the configured OAuth providers."""
    from app.integrations.oauth.factory import create_oauth_providers
    from app.services.oauth_flow import OAuthFlowService

    flow = OAuthFlowService(store, create_oauth_providers(settings))
    return RefreshingCredentialStore(store, flow)


def resolve_drive_credentials(store: OAuthCredentialStore, user_id: str) -> DriveCredentials:
    """Return decrypted Drive credentials for ``user_id`` or fail clearly."""
    credential = store.get(user_id, PROVIDER_GDRIVE)
    if credential is None:
        raise DriveConfigurationError(
            f"No stored Google Drive credential for user {user_id!r}. "
            "Add one to the credential store before enabling the MCP client."
        )
    return DriveCredentials(
        user_email=credential.account_label, access_token=credential.access_token
    )


def resolve_github_credentials(store: OAuthCredentialStore, user_id: str) -> GitHubCredentials:
    """Return decrypted GitHub credentials for ``user_id`` or fail clearly."""
    credential = store.get(user_id, PROVIDER_GITHUB)
    if credential is None:
        raise GitHubConfigurationError(
            f"No stored GitHub credential for user {user_id!r}. "
            "Add one to the credential store before enabling the MCP client."
        )
    return GitHubCredentials(access_token=credential.access_token)


def resolve_mail_credentials(store: OAuthCredentialStore, user_id: str) -> MailCredentials:
    """Return the Gmail sending identity for ``user_id`` or fail clearly.

    Gmail send reuses the stored Google credential (same account as Drive); the granted
    scopes must include ``gmail.send`` — add it to ``GOOGLE_OAUTH_SCOPES`` before
    connecting the account.
    """
    credential = store.get(user_id, PROVIDER_GDRIVE)
    if credential is None:
        raise MailConfigurationError(
            f"No stored Google credential for user {user_id!r}. Connect the Google "
            "account (with the gmail.send scope) before enabling GMAIL_ENABLED."
        )
    if credential.scopes and not any("gmail.send" in scope for scope in credential.scopes):
        raise MailConfigurationError(
            "The stored Google credential lacks the gmail.send scope. Reconnect the "
            "account with GOOGLE_OAUTH_SCOPES including "
            "https://www.googleapis.com/auth/gmail.send."
        )
    return MailCredentials(
        sender_email=credential.account_label, access_token=credential.access_token
    )


def resolve_inbox_credentials(store: OAuthCredentialStore, user_id: str) -> MailCredentials:
    """Return the Gmail read identity for ``user_id`` or fail clearly.

    The interview scan reuses the stored Google credential; its scopes must include
    ``gmail.readonly`` — add it to ``GOOGLE_OAUTH_SCOPES`` before connecting the account.
    """
    credential = store.get(user_id, PROVIDER_GDRIVE)
    if credential is None:
        raise MailConfigurationError(
            f"No stored Google credential for user {user_id!r}. Connect the Google "
            "account (with the gmail.readonly scope) before enabling the inbox scan."
        )
    if credential.scopes and not any("gmail.readonly" in scope for scope in credential.scopes):
        raise MailConfigurationError(
            "The stored Google credential lacks the gmail.readonly scope. Reconnect the "
            "account with GOOGLE_OAUTH_SCOPES including "
            "https://www.googleapis.com/auth/gmail.readonly."
        )
    return MailCredentials(
        sender_email=credential.account_label, access_token=credential.access_token
    )
