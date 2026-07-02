"""Selects the mail client implementation from config.

``GMAIL_ENABLED`` is the switch: false (default) -> in-process mock outbox, nothing can
leave the machine; true -> real Gmail client with the sending identity resolved from
the encrypted credential store. Callers depend only on the :class:`MailClient` interface.
"""

from __future__ import annotations

from app.config import Settings, get_settings
from app.domain.credentials import OAuthCredentialStore
from app.integrations.base import MailClient, MailConfigurationError


def create_mail_client(
    settings: Settings | None = None,
    *,
    store: OAuthCredentialStore | None = None,
    user_id: str | None = None,
) -> MailClient:
    """Return the configured mail client (mock by default, Gmail when enabled)."""
    settings = settings or get_settings()
    if settings.gmail_enabled:
        # Imported lazily so the mock path never depends on real-client code.
        from app.integrations.real.gmail import GmailMailClient
        from app.services.credentials import resolve_mail_credentials

        if store is None or user_id is None:
            raise MailConfigurationError(
                "GMAIL_ENABLED is true but no credential store/user was provided to "
                "resolve the sending identity."
            )
        credentials = resolve_mail_credentials(store, user_id)
        return GmailMailClient(credentials, send_url=settings.gmail_send_url)

    from app.integrations.mock.mail import MockMailClient

    return MockMailClient()
