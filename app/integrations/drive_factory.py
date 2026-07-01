"""Selects the Drive client implementation from config.

``GDRIVE_MCP_ENABLED`` is the switch: false (default) -> fixture-backed mock; true ->
MCP-backed adapter. Callers depend only on the :class:`DriveClient` interface, so the
rest of the app is unaffected by which one is returned.
"""

from __future__ import annotations

from app.config import Settings, get_settings
from app.integrations.base import DriveClient


def create_drive_client(settings: Settings | None = None) -> DriveClient:
    """Return the configured Drive client (mock by default, MCP when enabled)."""
    settings = settings or get_settings()
    if settings.gdrive_mcp_enabled:
        # Imported lazily so the mock path never depends on MCP code.
        from app.integrations.mcp.drive import McpDriveClient

        return McpDriveClient(settings)

    from app.integrations.mock.drive import MockDriveClient

    return MockDriveClient(settings.gdrive_mock_fixtures_dir)
