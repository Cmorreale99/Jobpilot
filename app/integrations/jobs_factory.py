"""Selects the job source implementation from config.

Only the fixture-backed mock exists today; the compliant real job API lands in M6 and
will slot in here behind a flag, the same way Drive/GitHub factories switch on their
MCP flags. Callers depend only on the :class:`JobSource` interface.
"""

from __future__ import annotations

from app.config import Settings, get_settings
from app.integrations.base import JobSource
from app.integrations.mock.jobs import MockJobSource


def create_job_source(settings: Settings | None = None) -> JobSource:
    """Return the configured job source (fixture-backed mock for now)."""
    settings = settings or get_settings()
    return MockJobSource(settings.jobs_mock_fixtures_dir)
