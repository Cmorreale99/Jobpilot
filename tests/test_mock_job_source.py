"""Unit tests for the fixture-backed MockJobSource."""

from __future__ import annotations

from datetime import UTC, datetime

from app.integrations.base import JobSource
from app.integrations.mock.jobs import MockJobSource


def test_source_satisfies_interface(mock_job_source: MockJobSource) -> None:
    assert isinstance(mock_job_source, JobSource)


async def test_fetch_all_jobs(mock_job_source: MockJobSource) -> None:
    jobs = await mock_job_source.fetch_recent_jobs()
    assert len(jobs) == 10
    assert {"job-1001", "job-1007"} <= {j.external_id for j in jobs}
    assert all(j.source == "mock" for j in jobs)


async def test_since_filter(mock_job_source: MockJobSource) -> None:
    since = datetime(2026, 6, 21, tzinfo=UTC)
    recent = {j.external_id for j in await mock_job_source.fetch_recent_jobs(since)}
    assert "job-1001" in recent  # posted 2026-06-28
    assert "job-1010" not in recent  # posted 2026-05-20
