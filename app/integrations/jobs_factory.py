"""Selects the job source implementation from config.

``JOB_SOURCE_PROVIDER`` is the switch: "mock" (default) -> fixture-backed source, fully
offline; "remotive" -> the compliant public Remotive API. LinkedIn deliberately has no
adapter (`ENABLE_LINKEDIN_SOURCE` stays a documented refusal — scraping violates their
ToS). Callers depend only on the :class:`JobSource` interface.
"""

from __future__ import annotations

from app.config import Settings, get_settings
from app.integrations.base import JobSource
from app.integrations.mock.jobs import MockJobSource


def create_job_source(settings: Settings | None = None) -> JobSource:
    """Return the configured job source (mock by default, Remotive when selected)."""
    settings = settings or get_settings()
    provider = settings.job_source_provider.strip().lower()
    if provider == "mock":
        return MockJobSource(settings.jobs_mock_fixtures_dir)
    if provider == "remotive":
        # Imported lazily so the mock path never depends on real-client code.
        from app.integrations.real.jobs_remotive import RemotiveJobSource

        return RemotiveJobSource(
            api_url=settings.remotive_api_url,
            search=settings.remotive_search,
            limit=settings.remotive_limit,
        )
    raise ValueError(
        f"Unknown JOB_SOURCE_PROVIDER {settings.job_source_provider!r} "
        "(expected 'mock' or 'remotive')."
    )
