"""Selects the GitHub client implementation from config.

``GITHUB_MCP_ENABLED`` is the switch: false (default) -> fixture-backed mock; true ->
MCP-backed adapter. Callers depend only on the :class:`GitHubClient` interface.
"""

from __future__ import annotations

from app.config import Settings, get_settings
from app.integrations.base import GitHubClient


def create_github_client(settings: Settings | None = None) -> GitHubClient:
    """Return the configured GitHub client (mock by default, MCP when enabled)."""
    settings = settings or get_settings()
    if settings.github_mcp_enabled:
        # Imported lazily so the mock path never depends on MCP code.
        from app.integrations.mcp.github import McpGitHubClient

        return McpGitHubClient(settings)

    from app.integrations.mock.github import MockGitHubClient

    return MockGitHubClient(settings.github_mock_fixtures_dir)
