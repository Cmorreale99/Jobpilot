"""The interview-scan job: scoped inbox search → detect → VERIFY → confirm queue.

The second of the two independent nightly jobs (a failure here never blocks the
application pipeline, and vice versa — the scheduler isolates them). Scheduler-free,
like :mod:`app.services.pipeline`, so it ports to EventBridge→Lambda untouched.

V2 anti-hallucination flow, mirroring the claims pipeline (deterministic provenance
check, then a human queue):

    detect invite (with verbatim body quote)
        -> re-fetch the message by id from the provider
        -> assert the quote is a substring of the REAL body (before any insert)
        -> failure: rejected + logged to validation_runs, no record ever created
        -> success: logged, interview lands in the CONFIRM QUEUE (stage=detected)

Prep packets are generated **only for confirmed interviews** — confirmation is the
human gate, so a detected-but-unconfirmed interview never spends prep effort. The scan
also back-fills packets for confirmed interviews that lack one (idempotent).

Privacy is enforced at three layers: ``INTERVIEW_INBOX_SCAN=false`` disables all inbox
reads; the search *query* scopes what a read can ever return; and the detector only
keeps messages with explicit invite language — everything else is dropped on the floor,
never stored.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.config import Settings, get_settings
from app.db.application_repository import SqlApplicationRepository
from app.db.interview_repository import SqlInterviewRepository
from app.db.master_cv_snapshot_store import SqlMasterCvSnapshotStore
from app.db.session import create_all, create_db_engine, create_session_factory
from app.db.validation_run_log import SqlValidationRunLog
from app.domain.applications import Application, ApplicationRepository
from app.domain.cv import MasterCv
from app.domain.interviews import (
    HeuristicInviteDetector,
    Interview,
    InterviewRepository,
    InterviewStage,
    InviteDetector,
    PrepPacketGenerator,
    normalize_company,
    verify_invite_provenance,
)
from app.domain.master_cv_snapshot import MasterCvSnapshotStore, master_cv_from_snapshot
from app.domain.validation_runs import KIND_INTERVIEW_VERIFICATION, ValidationRunLog
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
    snapshot_store: MasterCvSnapshotStore
    application_repository: ApplicationRepository
    validation_log: ValidationRunLog


@dataclass(frozen=True)
class InterviewScanResult:
    """What one scan did (for logging and tests)."""

    scanned: int
    detected: int
    verified: int
    rejected: int
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
        snapshot_store=SqlMasterCvSnapshotStore(session_factory),
        application_repository=SqlApplicationRepository(session_factory),
        validation_log=SqlValidationRunLog(session_factory),
    )


def _application_index(applications: list[Application]) -> dict[str, Application]:
    return {normalize_company(a.job_company): a for a in applications}


def generate_prep_packet(
    interview_repository: InterviewRepository,
    snapshot_store: MasterCvSnapshotStore,
    application_repository: ApplicationRepository,
    prep_generator: PrepPacketGenerator,
    interview: Interview,
    user_id: str,
) -> None:
    """Generate (or refresh) the prep packet for one CONFIRMED interview.

    The CV context comes from the approved-claims snapshot — prep packets, like every
    other generated artifact, are grounded in reviewed claims only.
    """
    if interview.stage is not InterviewStage.CONFIRMED:
        raise ValueError(
            f"prep packets are only generated for confirmed interviews (stage: {interview.stage})"
        )
    snapshot = snapshot_store.get_latest(user_id)
    master_cv = master_cv_from_snapshot(snapshot) if snapshot else MasterCv()
    applications = _application_index(application_repository.list_applications(user_id))
    application = applications.get(normalize_company(interview.company))
    packet = prep_generator.generate(interview, master_cv, application)
    interview_repository.save_prep_packet(packet)


def confirm_interview(
    interview_repository: InterviewRepository,
    snapshot_store: MasterCvSnapshotStore,
    application_repository: ApplicationRepository,
    prep_generator: PrepPacketGenerator,
    interview_id: int,
    user_id: str,
) -> Interview:
    """The human confirm action: ``detected -> confirmed``, then generate the packet."""
    interview = interview_repository.transition_interview(interview_id, InterviewStage.CONFIRMED)
    generate_prep_packet(
        interview_repository,
        snapshot_store,
        application_repository,
        prep_generator,
        interview,
        user_id,
    )
    return interview


async def run_interview_scan(
    deps: InterviewScanDependencies,
    settings: Settings | None = None,
    *,
    since: datetime | None = None,
    now: datetime | None = None,
) -> InterviewScanResult:
    """One scan: search (scoped), detect, verify provenance, queue for confirmation."""
    settings = settings or get_settings()
    user_id = settings.pipeline_user_id
    if not settings.interview_inbox_scan:
        logger.info("interview scan disabled (INTERVIEW_INBOX_SCAN=false); no inbox reads.")
        return InterviewScanResult(0, 0, 0, 0, 0, 0)

    now = now or datetime.now(tz=UTC)
    since = since or now - timedelta(hours=settings.interview_scan_since_hours)
    messages = await deps.inbox_scanner.search_messages(settings.interview_scan_query, since)
    logger.info(
        "interview scan[%s]: %d message(s) in scope since %s", user_id, len(messages), since
    )

    detected = verified = rejected = new_interviews = 0
    for message in messages:
        invite = deps.detector.detect(message)
        if invite is None:
            continue
        detected += 1

        # The anti-hallucination gate: re-fetch and verify BEFORE any insert.
        fetched = (
            await deps.inbox_scanner.get_message(message.message_id)
            if message.message_id.strip()
            else None
        )
        failure = verify_invite_provenance(message.message_id, invite.evidence_quote, fetched)
        deps.validation_log.record(
            user_id,
            KIND_INTERVIEW_VERIFICATION,
            subject_ref=f"message:{message.message_id or '(missing id)'}",
            passed=failure is None,
            detail=(failure,) if failure else (),
        )
        if failure is not None:
            rejected += 1
            logger.warning(
                "interview scan[%s]: rejected detection from %r: %s",
                user_id,
                message.sender,
                failure,
            )
            continue
        verified += 1

        interview, created = deps.interview_repository.upsert_interview(
            user_id, message.message_id, invite, message.received_at
        )
        if created:
            new_interviews += 1
            logger.info(
                "interview scan[%s]: %s (%s) verified -> confirm queue",
                user_id,
                interview.company,
                interview.job_title or "role not stated",
            )

    # Back-fill packets for confirmed interviews that lack one (idempotent catch-up);
    # detected-but-unconfirmed interviews never get one.
    packets = 0
    for interview in deps.interview_repository.list_interviews(user_id):
        if (
            interview.stage is InterviewStage.CONFIRMED
            and deps.interview_repository.get_prep_packet(interview.id) is None
        ):
            generate_prep_packet(
                deps.interview_repository,
                deps.snapshot_store,
                deps.application_repository,
                deps.prep_generator,
                interview,
                user_id,
            )
            packets += 1

    return InterviewScanResult(
        scanned=len(messages),
        detected=detected,
        verified=verified,
        rejected=rejected,
        new_interviews=new_interviews,
        packets_generated=packets,
    )
