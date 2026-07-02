"""The nightly application pipeline: refresh CV → match jobs → draft outreach.

This is the business-logic composition the scheduler triggers — deliberately free of
any APScheduler import so the same function ports to EventBridge→Lambda untouched.
It depends only on interfaces (clients, repositories), so it runs identically on mocks
(offline, zero credentials) and real integrations.

**Idempotent end to end**, because each stage already is: an unchanged Master CV adds
no version, matches are replaced per ``(user, cv version)``, and drafting refreshes
*drafted* rows only — re-running a night never duplicates rows, never re-queues a
decided draft, and never sends anything.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.config import Settings, get_settings
from app.db.application_repository import SqlApplicationRepository
from app.db.job_repository import SqlJobRepository
from app.db.master_cv_repository import SqlMasterCvRepository
from app.db.session import create_all, create_db_engine, create_session_factory
from app.domain.applications import ApplicationRepository
from app.domain.cv import MasterCvRepository
from app.domain.jobs import JobRepository
from app.integrations.base import (
    DriveClient,
    GitHubClient,
    JobSource,
    ResearchClient,
    UploadsClient,
)
from app.integrations.drive_factory import create_drive_client
from app.integrations.github_factory import create_github_client
from app.integrations.jobs_factory import create_job_source
from app.integrations.research_factory import create_research_client
from app.integrations.uploads import create_uploads_client
from app.services.master_cv_ingestion import refresh_master_cv
from app.services.matching import run_matching
from app.services.outreach import run_drafting

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineDependencies:
    """Everything the application pipeline needs, behind interfaces."""

    drive_client: DriveClient
    github_client: GitHubClient
    job_source: JobSource
    research_client: ResearchClient
    master_cv_repository: MasterCvRepository
    job_repository: JobRepository
    application_repository: ApplicationRepository
    uploads_client: UploadsClient | None = None


@dataclass(frozen=True)
class PipelineResult:
    """What one nightly run produced (for logging and tests)."""

    user_id: str
    master_cv_version: int
    master_cv_changed: bool
    match_count: int
    drafts_queued: int


def build_default_dependencies(settings: Settings | None = None) -> PipelineDependencies:
    """Composition root for a real run: factory-selected clients + SQL repositories.

    ``create_all`` is a dev convenience so tables exist without a migration step;
    production schema is owned by Alembic (see PLAN.md).
    """
    settings = settings or get_settings()
    engine = create_db_engine(settings)
    create_all(engine)
    session_factory = create_session_factory(engine)
    return PipelineDependencies(
        drive_client=create_drive_client(settings),
        github_client=create_github_client(settings),
        job_source=create_job_source(settings),
        research_client=create_research_client(settings),
        master_cv_repository=SqlMasterCvRepository(session_factory),
        job_repository=SqlJobRepository(session_factory),
        application_repository=SqlApplicationRepository(session_factory),
        uploads_client=create_uploads_client(settings),
    )


async def run_application_pipeline(
    deps: PipelineDependencies,
    settings: Settings | None = None,
    *,
    since: datetime | None = None,
    now: datetime | None = None,
) -> PipelineResult:
    """One nightly application-pipeline run for the configured user.

    Refresh the Master CV from all evidence sources, rank the fresh jobs against it,
    and draft tailored outreach into the approval queue. ``since`` defaults to the
    configured fresh-jobs window (``JOBS_SINCE_HOURS`` back from ``now``).
    """
    settings = settings or get_settings()
    user_id = settings.pipeline_user_id
    now = now or datetime.now(tz=UTC)
    since = since or now - timedelta(hours=settings.jobs_since_hours)

    version_before = deps.master_cv_repository.get_latest(user_id)
    stored = await refresh_master_cv(
        deps.drive_client,
        deps.github_client,
        user_id,
        deps.master_cv_repository,
        settings,
        uploads_client=deps.uploads_client,
        now=now,
    )
    changed = version_before is None or stored.version != version_before.version
    logger.info(
        "pipeline[%s]: master CV v%d (%s)",
        user_id,
        stored.version,
        "new version" if changed else "unchanged",
    )

    matches = await run_matching(
        deps.job_source, stored.master_cv, user_id, deps.job_repository, settings, since=since
    )
    logger.info("pipeline[%s]: %d deep-ranked matches since %s", user_id, len(matches), since)

    records = await run_drafting(
        user_id,
        stored.master_cv,
        matches,
        deps.research_client,
        deps.application_repository,
        settings,
    )
    logger.info("pipeline[%s]: %d outreach drafts in the approval queue", user_id, len(records))

    return PipelineResult(
        user_id=user_id,
        master_cv_version=stored.version,
        master_cv_changed=changed,
        match_count=len(matches),
        drafts_queued=len(records),
    )
