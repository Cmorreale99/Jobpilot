"""Selects the Drive client implementation from config.

``GDRIVE_MCP_ENABLED`` is the switch: false (default) -> fixture-backed mock; true ->
MCP-backed adapter. When the MCP client is selected and a credential ``store`` +
``user_id`` are supplied, the user's Drive credentials are resolved (decrypted) and
injected. Callers depend only on the :class:`DriveClient` interface.
"""

from __future__ import annotations

from app.config import Settings, get_settings
from app.domain.credentials import OAuthCredentialStore
from app.integrations.base import DriveClient


def create_drive_client(
    settings: Settings | None = None,
    *,
    store: OAuthCredentialStore | None = None,
    user_id: str | None = None,
) -> DriveClient:
    """Return the configured Drive client (mock by default, MCP when enabled)."""
    settings = settings or get_settings()
    if settings.gdrive_mcp_enabled:
        # Imported lazily so the mock path never depends on MCP/credential code.
        from app.integrations.mcp.drive import McpDriveClient
        from app.services.credentials import resolve_drive_credentials

        credentials = None
        if store is not None and user_id is not None:
            credentials = resolve_drive_credentials(store, user_id)
        return McpDriveClient(settings, credentials=credentials)

    from app.integrations.mock.drive import MockDriveClient

    return MockDriveClient(settings.gdrive_mock_fixtures_dir)
