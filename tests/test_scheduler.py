"""Scheduler wiring: trigger registration, job isolation, and the --once path."""

from __future__ import annotations

import logging

import pytest
from app.config import Settings
from app.domain.interviews import HeuristicInviteDetector, HeuristicPrepPacketGenerator
from app.integrations.mock.drive import MockDriveClient
from app.integrations.mock.github import MockGitHubClient
from app.integrations.mock.inbox import MockInboxScanner
from app.integrations.mock.jobs import MockJobSource
from app.integrations.mock.mail import MockMailClient
from app.integrations.mock.research import MockResearchClient
from app.scheduler import (
    APPLICATION_PIPELINE_JOB_ID,
    INTERVIEW_SCAN_JOB_ID,
    create_scheduler,
    main,
    run_job_safely,
)
from app.services.application_repository import InMemoryApplicationRepository
from app.services.interview_repository import InMemoryInterviewRepository
from app.services.interview_scan import InterviewScanDependencies
from app.services.job_repository import InMemoryJobRepository
from app.services.master_cv_repository import InMemoryMasterCvRepository
from app.services.pipeline import PipelineDependencies
from app.services.validation_run_log import InMemoryValidationRunLog

from tests.conftest import (
    FIXTURES_DIR,
    GITHUB_FIXTURES_DIR,
    INBOX_FIXTURES_DIR,
    JOBS_FIXTURES_DIR,
    RESEARCH_FIXTURES_DIR,
)


def _interview_deps() -> InterviewScanDependencies:
    return InterviewScanDependencies(
        inbox_scanner=MockInboxScanner(INBOX_FIXTURES_DIR),
        detector=HeuristicInviteDetector(),
        prep_generator=HeuristicPrepPacketGenerator(),
        interview_repository=InMemoryInterviewRepository(),
        master_cv_repository=InMemoryMasterCvRepository(),
        application_repository=InMemoryApplicationRepository(),
        validation_log=InMemoryValidationRunLog(),
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


def test_scheduler_registers_both_nightly_triggers(settings: Settings) -> None:
    configured = settings.model_copy(
        update={
            "pipeline_hour": 3,
            "pipeline_minute": 30,
            "interview_scan_hour": 4,
            "interview_scan_minute": 15,
        }
    )
    scheduler = create_scheduler(configured)
    jobs = {job.id: str(job.trigger) for job in scheduler.get_jobs()}
    assert set(jobs) == {APPLICATION_PIPELINE_JOB_ID, INTERVIEW_SCAN_JOB_ID}
    assert "hour='3'" in jobs[APPLICATION_PIPELINE_JOB_ID]
    assert "minute='30'" in jobs[APPLICATION_PIPELINE_JOB_ID]
    assert "hour='4'" in jobs[INTERVIEW_SCAN_JOB_ID]
    assert "minute='15'" in jobs[INTERVIEW_SCAN_JOB_ID]


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
        ["--once", "--job", "pipeline"],
        settings=settings.model_copy(update={"jobs_since_hours": 24 * 30}),
        dependencies=deps,
    )
    assert exit_code == 0
    assert deps.application_repository.list_pending_outreach("u1")


def test_main_once_reports_failure_without_crashing(settings: Settings) -> None:
    deps = _deps(job_source=_ExplodingJobSource())
    exit_code = main(["--once", "--job", "pipeline"], settings=settings, dependencies=deps)
    assert exit_code == 1  # failure surfaced in the exit code, not a traceback


def test_main_once_runs_interview_scan(settings: Settings) -> None:
    ideps = _interview_deps()
    exit_code = main(
        ["--once", "--job", "interviews"],
        settings=settings.model_copy(update={"interview_scan_since_hours": 24 * 365}),
        interview_dependencies=ideps,
    )
    assert exit_code == 0
    assert ideps.interview_repository.list_interviews("u1")


def test_main_once_all_isolates_a_failing_job(settings: Settings) -> None:
    # Pipeline explodes; the interview scan still runs to completion.
    deps = _deps(job_source=_ExplodingJobSource())
    ideps = _interview_deps()
    exit_code = main(
        ["--once"],
        settings=settings.model_copy(update={"interview_scan_since_hours": 24 * 365}),
        dependencies=deps,
        interview_dependencies=ideps,
    )
    assert exit_code == 1  # the failure is reported...
    assert ideps.interview_repository.list_interviews("u1")  # ...but didn't block the scan
