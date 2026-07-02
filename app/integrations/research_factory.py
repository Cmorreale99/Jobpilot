"""Selects the contact-research client implementation from config.

Only the fixture-backed mock exists today; a real research client (M6+) will slot in
here behind a flag. Callers depend only on the :class:`ResearchClient` interface.
"""

from __future__ import annotations

from app.config import Settings, get_settings
from app.integrations.base import ResearchClient
from app.integrations.mock.research import MockResearchClient


def create_research_client(settings: Settings | None = None) -> ResearchClient:
    """Return the configured research client (fixture-backed mock for now)."""
    settings = settings or get_settings()
    return MockResearchClient(settings.research_mock_fixtures_dir)
