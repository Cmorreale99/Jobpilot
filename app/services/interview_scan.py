"""The interview-scan job: scoped inbox search → detect invites → prep packets.

The second of the two independent nightly jobs (a failure here never blocks the
application pipeline, and vice versa — the scheduler isolates them). Scheduler-free,
like :mod:`app.services.pipeline`, so it ports to EventBridge→Lambda untouched.

Privacy is enforced at three layers: ``INTERVIEW_INBOX_SCAN=false`` disables all inbox
reads; the search *query* scopes what a read can ever return; and the detector only
keeps messages with explicit invite language — everything else is dropped on the floor,
never stored.

**Idempotent:** interviews dedupe on the source message id, re-scans never regress a
stage the user advanced, and packets are only generated for interviews that lack one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.config import Settings, get_settings
from app.db.application_repository import SqlApplicationRepository
from app.db.interview_repository import SqlInterviewRepository
from app.db.master_cv_repository import SqlMasterCvRepository
from app.db.session import create_all, create_db_engine, create_session_factory
from app.domain.applications import Application, ApplicationRepository
from app.domain.cv import MasterCv, MasterCvRepository
from app.domain.interviews import (
    HeuristicInviteDetector,
    InterviewRepository,
    InviteDetector,
    PrepPacketGenerator,
    normalize_company,
)
from app.integrations.base import InboxScanner
from app.integrations.inbox_factory import create_inbox_scanner
from app.services.prep_factory import create_prep_generator

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InterviewScanDependencies:
    """Everything the interview scan needs, behind interfaces."""

    inbox_scanner: InboxScanner
    detector: InviteDetector
    prep_generator: PrepPacketGenerator
    interview_repository: InterviewRepository
    master_cv_repository: MasterCvRepository
    application_repository: ApplicationRepository


@dataclass(frozen=True)
class InterviewScanResult:
    """What one scan did (for logging and tests)."""

    scanned: int
    detected: int
    new_interviews: int
    packets_generated: int


def build_default_interview_dependencies(
    settings: Settings | None = None,
) -> InterviewScanDependencies:
    """Composition root for a real run: factory-selected scanner + SQL repositories."""
    settings = settings or get_settings()
    engine = create_db_engine(settings)
    create_all(engine)
    session_factory = create_session_factory(engine)

    # The real Gmail scanner needs the encrypted credential store; the mock needs nothing.
    # Reads go through the refreshing view so a near-expiry Google token is renewed
    # before the scan uses it.
    store = None
    if settings.gmail_enabled:
        from app.db.credentials_store import SqlOAuthCredentialStore
        from app.security.crypto import TokenCipher
        from app.services.credentials import create_refreshing_store

        store = create_refreshing_store(
            SqlOAuthCredentialStore(session_factory, TokenCipher.from_settings(settings)),
            settings,
        )

    return InterviewScanDependencies(
        inbox_scanner=create_inbox_scanner(
            settings, store=store, user_id=settings.pipeline_user_id
        ),
        detector=HeuristicInviteDetector(),
        prep_generator=create_prep_generator(settings),
        interview_repository=SqlInterviewRepository(session_factory),
        master_cv_repository=SqlMasterCvRepository(session_factory),
        application_repository=SqlApplicationRepository(session_factory),
    )


def _application_index(applications: list[Application]) -> dict[str, Application]:
    return {normalize_company(a.job_company): a for a in applications}


async def run_interview_scan(
    deps: InterviewScanDependencies,
    settings: Settings | None = None,
    *,
    since: datetime | None = None,
    now: datetime | None = None,
) -> InterviewScanResult:
    """One scan: search the inbox (scoped), record new interviews, generate packets."""
    settings = settings or get_settings()
    user_id = settings.pipeline_user_id
    if not settings.interview_inbox_scan:
        logger.info("interview scan disabled (INTERVIEW_INBOX_SCAN=false); no inbox reads.")
        return InterviewScanResult(scanned=0, detected=0, new_interviews=0, packets_generated=0)

    now = now or datetime.now(tz=UTC)
    since = since or now - timedelta(hours=settings.interview_scan_since_hours)
    messages = await deps.inbox_scanner.search_messages(settings.interview_scan_query, since)
    logger.info(
        "interview scan[%s]: %d message(s) in scope since %s", user_id, len(messages), since
    )

    stored_cv = deps.master_cv_repository.get_latest(user_id)
    master_cv = stored_cv.master_cv if stored_cv else MasterCv()
    applications = _application_index(deps.application_repository.list_applications(user_id))

    detected = new_interviews = packets = 0
    for message in messages:
        invite = deps.detector.detect(message)
        if invite is None:
            continue
        detected += 1
        interview, created = deps.interview_repository.upsert_interview(
            user_id, message.message_id, invite, message.received_at
        )
        if created:
            new_interviews += 1
            logger.info(
                "interview scan[%s]: new interview at %s (%s)",
                user_id,
                interview.company,
                interview.job_title or "role not stated",
            )
        if created or deps.interview_repository.get_prep_packet(interview.id) is None:
            application = applications.get(normalize_company(invite.company))
            packet = deps.prep_generator.generate(interview, master_cv, application)
            deps.interview_repository.save_prep_packet(packet)
            packets += 1

    return InterviewScanResult(
        scanned=len(messages),
        detected=detected,
        new_interviews=new_interviews,
        packets_generated=packets,
    )
