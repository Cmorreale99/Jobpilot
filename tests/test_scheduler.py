"""Scheduler wiring: trigger registration, job isolation, and the --once path."""

from __future__ import annotations

import logging

import pytest
from app.config import Settings
from app.integrations.mock.drive import MockDriveClient
from app.integrations.mock.github import MockGitHubClient
from app.integrations.mock.jobs import MockJobSource
from app.integrations.mock.mail import MockMailClient
from app.integrations.mock.research import MockResearchClient
from app.scheduler import APPLICATION_PIPELINE_JOB_ID, create_scheduler, main, run_job_safely
from app.services.application_repository import InMemoryApplicationRepository
from app.services.job_repository import InMemoryJobRepository
from app.services.master_cv_repository import InMemoryMasterCvRepository
from app.services.pipeline import PipelineDependencies

from tests.conftest import (
    FIXTURES_DIR,
    GITHUB_FIXTURES_DIR,
    JOBS_FIXTURES_DIR,
    RESEARCH_FIXTURES_DIR,
)


def _deps(job_source: object | None = None) -> PipelineDependencies:
    return PipelineDependencies(
        drive_client=MockDriveClient(FIXTURES_DIR),
        github_client=MockGitHubClient(GITHUB_FIXTURES_DIR),
        job_source=job_source or MockJobSource(JOBS_FIXTURES_DIR),  # type: ignore[arg-type]
        research_client=MockResearchClient(RESEARCH_FIXTURES_DIR),
        mail_client=MockMailClient(),
        master_cv_repository=InMemoryMasterCvRepository(),
        job_repository=InMemoryJobRepository(),
        application_repository=InMemoryApplicationRepository(),
    )


def test_scheduler_registers_nightly_trigger(settings: Settings) -> None:
    configured = settings.model_copy(update={"pipeline_hour": 3, "pipeline_minute": 30})
    scheduler = create_scheduler(configured)
    (job,) = scheduler.get_jobs()
    assert job.id == APPLICATION_PIPELINE_JOB_ID
    trigger = str(job.trigger)
    assert "hour='3'" in trigger
    assert "minute='30'" in trigger


async def test_run_job_safely_returns_result() -> None:
    async def fine() -> str:
        return "done"

    assert await run_job_safely("demo", fine) == "done"


async def test_run_job_safely_isolates_failures(caplog: pytest.LogCaptureFixture) -> None:
    async def broken() -> str:
        raise RuntimeError("nightly explosion")

    with caplog.at_level(logging.ERROR):
        result = await run_job_safely("demo", broken)  # must not raise
    assert result is None
    assert any("demo" in r.message for r in caplog.records)


class _ExplodingJobSource:
    async def fetch_recent_jobs(self, since: object = None) -> list[object]:
        raise ConnectionError("job API unreachable")


def test_main_once_runs_pipeline_and_reports_success(settings: Settings) -> None:
    deps = _deps()
    exit_code = main(
        ["--once"],
        settings=settings.model_copy(update={"jobs_since_hours": 24 * 30}),
        dependencies=deps,
    )
    assert exit_code == 0
    assert deps.application_repository.list_pending_outreach("u1")


def test_main_once_reports_failure_without_crashing(settings: Settings) -> None:
    deps = _deps(job_source=_ExplodingJobSource())
    exit_code = main(["--once"], settings=settings, dependencies=deps)
    assert exit_code == 1  # failure surfaced in the exit code, not a traceback
