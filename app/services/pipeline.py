"""The nightly application pipeline: snapshot approved claims → match jobs → draft outreach.

This is the business-logic composition the scheduler triggers — deliberately free of
any APScheduler import so the same function ports to EventBridge→Lambda untouched.
It depends only on interfaces (clients, repositories), so it runs identically on mocks
(offline, zero credentials) and real integrations.

**V2 only.** The Master CV the pipeline matches and drafts from is a snapshot of
human-approved claims — the retired V1 ingestion path (build-from-raw-evidence, no
review) is never invoked, so nothing unreviewed can reach matching, tailoring, or
outreach. With zero approved claims the pipeline stops before matching: no prose is
ever generated from an empty or unreviewed ledger.

**Idempotent end to end**, because each stage already is: unchanged approved claims add
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
from app.db.claim_repository import SqlClaimRepository
from app.db.job_repository import SqlJobRepository
from app.db.master_cv_snapshot_store import SqlMasterCvSnapshotStore
from app.db.session import create_all, create_db_engine, create_session_factory
from app.domain.applications import ApplicationRepository, OutreachStatus
from app.domain.claims import ClaimRepository
from app.domain.jobs import JobRepository
from app.domain.master_cv_snapshot import MasterCvSnapshotStore, master_cv_from_snapshot
from app.integrations.base import JobSource, MailClient, ResearchClient
from app.integrations.jobs_factory import create_job_source
from app.integrations.mail_factory import create_mail_client
from app.integrations.research_factory import create_research_client
from app.services.master_cv_snapshot import create_master_cv_snapshot
from app.services.matching import run_matching
from app.services.outreach import run_drafting
from app.services.outreach_send import send_approved_outreach

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineDependencies:
    """Everything the application pipeline needs, behind interfaces."""

    job_source: JobSource
    research_client: ResearchClient
    mail_client: MailClient
    claim_repository: ClaimRepository
    snapshot_store: MasterCvSnapshotStore
    job_repository: JobRepository
    application_repository: ApplicationRepository


@dataclass(frozen=True)
class PipelineResult:
    """What one nightly run produced (for logging and tests)."""

    user_id: str
    master_cv_version: int
    master_cv_changed: bool
    match_count: int
    drafts_queued: int
    drafts_sent: int = 0  # only ever non-zero with OUTREACH_AUTO_SEND=true


def build_default_dependencies(settings: Settings | None = None) -> PipelineDependencies:
    """Composition root for a real run: factory-selected clients + SQL repositories.

    ``create_all`` is a dev convenience so tables exist without a migration step;
    production schema is owned by Alembic (see PLAN.md).
    """
    settings = settings or get_settings()
    engine = create_db_engine(settings)
    create_all(engine)
    session_factory = create_session_factory(engine)

    # The real mail client needs the encrypted credential store; the mock needs nothing.
    # Reads go through the refreshing view so a near-expiry Google token is renewed
    # before the nightly run uses it.
    store = None
    if settings.gmail_enabled:
        from app.db.credentials_store import SqlOAuthCredentialStore
        from app.security.crypto import TokenCipher
        from app.services.credentials import create_refreshing_store

        store = create_refreshing_store(
            SqlOAuthCredentialStore(session_factory, TokenCipher.from_settings(settings)),
            settings,
        )

    return PipelineDependencies(
        job_source=create_job_source(settings),
        research_client=create_research_client(settings),
        mail_client=create_mail_client(settings, store=store, user_id=settings.pipeline_user_id),
        claim_repository=SqlClaimRepository(session_factory),
        snapshot_store=SqlMasterCvSnapshotStore(session_factory),
        job_repository=SqlJobRepository(session_factory),
        application_repository=SqlApplicationRepository(session_factory),
    )


async def run_application_pipeline(
    deps: PipelineDependencies,
    settings: Settings | None = None,
    *,
    since: datetime | None = None,
    now: datetime | None = None,
) -> PipelineResult:
    """One nightly application-pipeline run for the configured user.

    Snapshot the approved claims as the current Master CV version (idempotent), rank
    the fresh jobs against it, and draft tailored outreach into the approval queue.
    ``since`` defaults to the configured fresh-jobs window (``JOBS_SINCE_HOURS`` back
    from ``now``). With no approved claims the run stops after logging — matching and
    drafting never see unreviewed content.
    """
    settings = settings or get_settings()
    user_id = settings.pipeline_user_id
    now = now or datetime.now(tz=UTC)
    since = since or now - timedelta(hours=settings.jobs_since_hours)

    version_before = deps.snapshot_store.get_latest(user_id)
    snapshot = create_master_cv_snapshot(user_id, deps.claim_repository, deps.snapshot_store)
    changed = version_before is None or snapshot.version != version_before.version
    master_cv = master_cv_from_snapshot(snapshot)
    logger.info(
        "pipeline[%s]: master CV v%d (%s), %d approved claims",
        user_id,
        snapshot.version,
        "new version" if changed else "unchanged",
        len(master_cv.claims),
    )

    if not master_cv.claims:
        logger.warning(
            "pipeline[%s]: no approved claims — skipping matching and drafting "
            "(review and approve claims first; nothing is generated from an unreviewed ledger)",
            user_id,
        )
        return PipelineResult(
            user_id=user_id,
            master_cv_version=snapshot.version,
            master_cv_changed=changed,
            match_count=0,
            drafts_queued=0,
        )

    matches = await run_matching(
        deps.job_source, master_cv, user_id, deps.job_repository, settings, since=since
    )
    logger.info("pipeline[%s]: %d deep-ranked matches since %s", user_id, len(matches), since)

    records = await run_drafting(
        user_id,
        master_cv,
        matches,
        deps.research_client,
        deps.application_repository,
        settings,
    )
    logger.info("pipeline[%s]: %d outreach drafts in the approval queue", user_id, len(records))

    # Auto-send is opt-in and explicit: the fresh drafts are approved programmatically
    # and every approved draft with a researched address is sent. With the default mock
    # mail client nothing leaves the process; real sends additionally require
    # GMAIL_ENABLED plus a stored Google credential with the gmail.send scope.
    sent = 0
    if settings.outreach_auto_send:
        for record in records:
            if record.status is OutreachStatus.DRAFTED:
                deps.application_repository.transition_outreach(record.id, OutreachStatus.APPROVED)
        report = await send_approved_outreach(
            user_id, deps.application_repository, deps.mail_client
        )
        sent = len(report.sent)
        logger.info(
            "pipeline[%s]: auto-send sent %d, skipped %d (no address), failed %d",
            user_id,
            sent,
            len(report.skipped_no_address),
            len(report.failed),
        )

    return PipelineResult(
        user_id=user_id,
        master_cv_version=snapshot.version,
        master_cv_changed=changed,
        match_count=len(matches),
        drafts_queued=len(records),
        drafts_sent=sent,
    )
