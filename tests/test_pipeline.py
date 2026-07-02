"""Nightly application pipeline on mocks: end-to-end flow and idempotent re-runs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.config import Settings
from app.domain.applications import ApplicationStatus, OutreachStatus
from app.integrations.mock.drive import MockDriveClient
from app.integrations.mock.github import MockGitHubClient
from app.integrations.mock.jobs import MockJobSource
from app.integrations.mock.research import MockResearchClient
from app.services.application_repository import InMemoryApplicationRepository
from app.services.job_repository import InMemoryJobRepository
from app.services.master_cv_repository import InMemoryMasterCvRepository
from app.services.pipeline import PipelineDependencies, run_application_pipeline

from tests.conftest import (
    FIXTURES_DIR,
    GITHUB_FIXTURES_DIR,
    JOBS_FIXTURES_DIR,
    RESEARCH_FIXTURES_DIR,
)

# Fixture jobs are posted late June 2026; this window includes them all.
_NOW = datetime(2026, 7, 1, 2, 0, tzinfo=UTC)


@pytest.fixture
def deps() -> PipelineDependencies:
    return PipelineDependencies(
        drive_client=MockDriveClient(FIXTURES_DIR),
        github_client=MockGitHubClient(GITHUB_FIXTURES_DIR),
        job_source=MockJobSource(JOBS_FIXTURES_DIR),
        research_client=MockResearchClient(RESEARCH_FIXTURES_DIR),
        master_cv_repository=InMemoryMasterCvRepository(),
        job_repository=InMemoryJobRepository(),
        application_repository=InMemoryApplicationRepository(),
    )


@pytest.fixture
def pipeline_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"top_n": 4, "shortlist_size": 10, "jobs_since_hours": 24 * 30}
    )


async def test_pipeline_runs_end_to_end(
    deps: PipelineDependencies, pipeline_settings: Settings
) -> None:
    result = await run_application_pipeline(deps, pipeline_settings, now=_NOW)

    assert result.user_id == "u1"
    assert result.master_cv_version == 1
    assert result.master_cv_changed is True
    assert result.match_count == 4  # top_n
    assert result.drafts_queued == 4

    # Everything landed where the dashboard reads it.
    assert deps.job_repository.get_matches("u1", 1)
    queue = deps.application_repository.list_pending_outreach("u1")
    assert len(queue) == 4
    applications = deps.application_repository.list_applications("u1")
    assert all(a.status is ApplicationStatus.DRAFTED for a in applications)
    assert all(a.materials.highlights for a in applications)


async def test_rerun_is_fully_idempotent(
    deps: PipelineDependencies, pipeline_settings: Settings
) -> None:
    first = await run_application_pipeline(deps, pipeline_settings, now=_NOW)
    second = await run_application_pipeline(deps, pipeline_settings, now=_NOW)

    # Unchanged evidence -> same CV version, no new rows anywhere.
    assert second.master_cv_version == first.master_cv_version
    assert second.master_cv_changed is False
    assert deps.master_cv_repository.list_versions("u1") == [1]
    assert len(deps.application_repository.list_applications("u1")) == first.drafts_queued
    first_queue = deps.application_repository.list_pending_outreach("u1")
    assert len(first_queue) == first.drafts_queued  # replaced in place, not duplicated


async def test_rerun_preserves_human_decisions(
    deps: PipelineDependencies, pipeline_settings: Settings
) -> None:
    await run_application_pipeline(deps, pipeline_settings, now=_NOW)
    queue = deps.application_repository.list_pending_outreach("u1")
    approved = deps.application_repository.transition_outreach(queue[0].id, OutreachStatus.APPROVED)
    application = deps.application_repository.list_applications("u1")[1]
    deps.application_repository.transition_application(application.id, ApplicationStatus.APPLIED)

    await run_application_pipeline(deps, pipeline_settings, now=_NOW)

    still_approved = deps.application_repository.get_outreach(approved.id)
    assert still_approved is not None and still_approved.status is OutreachStatus.APPROVED
    reread = deps.application_repository.get_application(application.id)
    assert reread is not None and reread.status is ApplicationStatus.APPLIED
    # The approved draft left the queue. The applied application's own draft stays
    # pending — applying does not silently discard an undecided outreach message.
    assert len(deps.application_repository.list_pending_outreach("u1")) == 3


async def test_fresh_jobs_window_is_honored(
    deps: PipelineDependencies, pipeline_settings: Settings
) -> None:
    # A `since` after every fixture posting -> nothing fetched, nothing drafted.
    result = await run_application_pipeline(
        deps, pipeline_settings, since=_NOW + timedelta(days=1), now=_NOW
    )
    assert result.match_count == 0
    assert result.drafts_queued == 0
    assert result.master_cv_version == 1  # the CV still refreshed


async def test_default_window_comes_from_settings(
    deps: PipelineDependencies, pipeline_settings: Settings
) -> None:
    # 24h window from _NOW covers only the postings from the last day of June.
    narrow = pipeline_settings.model_copy(update={"jobs_since_hours": 24})
    result = await run_application_pipeline(deps, narrow, now=_NOW)
    wide = await run_application_pipeline(
        deps, pipeline_settings, now=_NOW
    )  # 30-day window sees more
    assert result.match_count <= wide.match_count
