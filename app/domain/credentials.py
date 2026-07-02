"""OAuth credential domain types and the store contract.

Pure logic — no crypto, no DB. A :class:`OAuthCredential` is the *decrypted* value that
flows through the app; encryption at rest is an implementation detail of the store.
Callers of :class:`OAuthCredentialStore` never see ciphertext.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

# Provider identifiers, aligned with cv_sources.source_type where applicable.
PROVIDER_GDRIVE = "gdrive"
PROVIDER_GITHUB = "github"


@dataclass(frozen=True)
class OAuthCredential:
    """A user's decrypted OAuth credential for one provider.

    ``account_label`` is the provider-facing identity — a Google account email for
    ``gdrive`` (the Workspace MCP's ``user_google_email``), or a GitHub username.
    """

    user_id: str
    provider: str
    account_label: str
    access_token: str
    refresh_token: str | None = None
    scopes: tuple[str, ...] = ()
    expires_at: datetime | None = None


@runtime_checkable
class OAuthCredentialStore(Protocol):
    """Persistence for OAuth credentials, encrypting tokens at rest.

    The interface trades in decrypted :class:`OAuthCredential` values; implementations
    encrypt on write and decrypt on read.
    """

    def get(self, user_id: str, provider: str) -> OAuthCredential | None:
        """Return the stored credential for ``(user_id, provider)`` or ``None``."""
        ...

    def upsert(self, credential: OAuthCredential) -> None:
        """Insert or replace the credential for its ``(user_id, provider)``."""
        ...

    def delete(self, user_id: str, provider: str) -> None:
        """Remove the credential for ``(user_id, provider)`` if present."""
        ...
