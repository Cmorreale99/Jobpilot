"""Credential resolution and the in-memory (mock-first) credential store.

* :class:`InMemoryOAuthCredentialStore` — a fixture-friendly store that still encrypts
  tokens at rest (so tests exercise the crypto path), used when no database is wired.
* ``resolve_*_credentials`` — map a stored :class:`OAuthCredential` onto the
  integration-specific credential type each MCP client needs.

The resolvers fail with the client's own configuration error when a credential is
missing, so an unconfigured provider is reported clearly rather than silently skipped.
"""

from __future__ import annotations

from app.domain.credentials import (
    PROVIDER_GDRIVE,
    PROVIDER_GITHUB,
    OAuthCredential,
    OAuthCredentialStore,
)
from app.integrations.base import (
    DriveConfigurationError,
    DriveCredentials,
    GitHubConfigurationError,
    GitHubCredentials,
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
