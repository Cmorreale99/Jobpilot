"""Selects the GitHub client implementation from config.

``GITHUB_MCP_ENABLED`` is the switch: false (default) -> fixture-backed mock; true ->
MCP-backed adapter. When the MCP client is selected and a credential ``store`` +
``user_id`` are supplied, the user's GitHub PAT is resolved (decrypted) and injected.
Callers depend only on the :class:`GitHubClient` interface.
"""

from __future__ import annotations

from app.config import Settings, get_settings
from app.domain.credentials import OAuthCredentialStore
from app.integrations.base import GitHubClient


def create_github_client(
    settings: Settings | None = None,
    *,
    store: OAuthCredentialStore | None = None,
    user_id: str | None = None,
) -> GitHubClient:
    """Return the configured GitHub client (mock by default, MCP when enabled)."""
    settings = settings or get_settings()
    if settings.github_mcp_enabled:
        # Imported lazily so the mock path never depends on MCP/credential code.
        from app.integrations.mcp.github import McpGitHubClient
        from app.services.credentials import resolve_github_credentials

        credentials = None
        if store is not None and user_id is not None:
            credentials = resolve_github_credentials(store, user_id)
        return McpGitHubClient(settings, credentials=credentials)

    from app.integrations.mock.github import MockGitHubClient

    return MockGitHubClient(settings.github_mock_fixtures_dir)
