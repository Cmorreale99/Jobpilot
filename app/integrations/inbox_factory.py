"""Selects the inbox scanner implementation from config.

``GMAIL_ENABLED`` is the switch (same Google account and credential as sending): false
(default) -> fixture-backed mock, fully offline; true -> the real read-only Gmail
scanner, whose stored credential must carry the gmail.readonly scope. Whether scanning
happens *at all* is a separate gate — ``INTERVIEW_INBOX_SCAN`` in the scan service.
"""

from __future__ import annotations

from app.config import Settings, get_settings
from app.domain.credentials import OAuthCredentialStore
from app.integrations.base import InboxConfigurationError, InboxScanner


def create_inbox_scanner(
    settings: Settings | None = None,
    *,
    store: OAuthCredentialStore | None = None,
    user_id: str | None = None,
) -> InboxScanner:
    """Return the configured inbox scanner (mock by default, Gmail when enabled)."""
    settings = settings or get_settings()
    if settings.gmail_enabled:
        # Imported lazily so the mock path never depends on real-client code.
        from app.integrations.real.gmail_inbox import GmailInboxScanner
        from app.services.credentials import resolve_inbox_credentials

        if store is None or user_id is None:
            raise InboxConfigurationError(
                "GMAIL_ENABLED is true but no credential store/user was provided to "
                "resolve the inbox credential."
            )
        return GmailInboxScanner(resolve_inbox_credentials(store, user_id))

    from app.integrations.mock.inbox import MockInboxScanner

    return MockInboxScanner(settings.inbox_mock_fixtures_dir)
