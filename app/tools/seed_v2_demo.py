"""Seed the dev database with the V2 fixture loop — ``python -m app.tools.seed_v2_demo``.

Confirms a per-source demo roster, runs claim extraction over the V2 claims fixtures
(mock Drive + GitHub), and runs the interview scan over the inbox fixtures (mock
scanner), persisting into the configured ``DATABASE_URL``. Zero credentials, zero
network — regardless of what the local ``.env`` enables, this tool always uses the
mocks (it is a demo seeder, not a live run).

After seeding, the dashboard shows: pending claims in the review queue (one clean, one
coupling-flagged, commit-derived ones missing results) and two verified interviews in
the confirm queue. Idempotent: re-running refreshes unreviewed rows only.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings, get_settings
from app.db.application_repository import SqlApplicationRepository
from app.db.claim_repository import SqlClaimRepository
from app.db.interview_repository import SqlInterviewRepository
from app.db.master_cv_snapshot_store import SqlMasterCvSnapshotStore
from app.db.session import create_all, create_db_engine, create_session_factory
from app.db.validation_run_log import SqlValidationRunLog
from app.domain.chunking import chunk_normalized_text
from app.domain.claims import (
    SOURCE_GITHUB_COMMIT,
    ClaimRepository,
    EvidenceChunk,
    ExperienceStatus,
    span_ref,
)
from app.domain.interviews import HeuristicInviteDetector, HeuristicPrepPacketGenerator
from app.domain.roster import HeuristicRosterProposer
from app.integrations.base import DriveClient, GitHubClient
from app.integrations.mock.drive import MockDriveClient
from app.integrations.mock.github import MockGitHubClient
from app.integrations.mock.inbox import MockInboxScanner
from app.services.claim_extraction import run_claim_extraction
from app.services.interview_scan import InterviewScanDependencies, run_interview_scan
from app.services.roster import gather_source_documents

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CLAIMS_FIXTURES = _REPO_ROOT / "tests" / "fixtures" / "claims"
_INBOX_FIXTURES = _REPO_ROOT / "tests" / "fixtures" / "inbox"


async def _confirm_fixture_roster(
    drive_client: DriveClient,
    github_client: GitHubClient,
    user_id: str,
    repository: ClaimRepository,
    settings: Settings,
) -> int:
    """The demo's roster review: adopt each fixture source as its own confirmed entity.

    Extraction refuses to run without a confirmed roster with assigned evidence (V3
    Phase 0 deleted the per-file fallback), so the seeder confirms the fixture
    proposals explicitly and assigns each source's chunks to its own entity.
    Twin logic lives in ``tests/conftest.py::confirm_source_roster``.
    """
    documents = (
        await gather_source_documents(drive_client, github_client, user_id, settings)
    ).documents
    confirmed: dict[str, int] = {}
    for proposal in HeuristicRosterProposer().propose(documents):
        experience = repository.propose_experience(user_id, proposal.to_seed())
        repository.set_experience_status(experience.id, ExperienceStatus.CONFIRMED)
        confirmed[proposal.name.casefold()] = experience.id
    for document in documents:
        target = confirmed.get(document.title.casefold())
        if document.source_type == SOURCE_GITHUB_COMMIT:
            stored = repository.upsert_evidence(
                user_id, EvidenceChunk(document.source_type, document.source_ref, document.text)
            )
            repository.assign_evidence(stored.id, target)
            continue
        for piece in chunk_normalized_text(document.text):
            stored = repository.upsert_evidence(
                user_id,
                EvidenceChunk(
                    document.source_type,
                    span_ref(document.source_ref, piece.start, piece.end),
                    piece.text,
                ),
            )
            repository.assign_evidence(stored.id, target)
    return len(confirmed)


async def _seed() -> None:
    # Fixture-scoped settings: whatever the local .env enables, this demo stays on
    # mocks and the fixture policy scope.
    settings = get_settings().model_copy(
        update={
            "gdrive_source_folder_id": "career_docs",
            "gdrive_allow_broad_scan": False,
            "github_username": "jordanrivera",
            "github_allow_broad_scan": False,
            "claims_llm_extraction": False,
            "interview_inbox_scan": True,
        }
    )
    user_id = settings.pipeline_user_id

    engine = create_db_engine(settings)
    create_all(engine)
    session_factory = create_session_factory(engine)

    claim_repo = SqlClaimRepository(session_factory)
    drive_client = MockDriveClient(_CLAIMS_FIXTURES / "drive")
    github_client = MockGitHubClient(_CLAIMS_FIXTURES / "github")
    entities = await _confirm_fixture_roster(
        drive_client, github_client, user_id, claim_repo, settings
    )
    print(f"roster: {entities} fixture entities confirmed")
    report = await run_claim_extraction(
        user_id,
        claim_repo,
        settings,
        validation_log=SqlValidationRunLog(session_factory),
    )
    print(
        f"claims: {len(report.claims)} pending review "
        f"({len(report.flagged)} flagged, {len(report.missing_results)} missing results; "
        f"{len(report.dropped)} dropped, {len(report.deduped)} deduped)"
    )

    interview_deps = InterviewScanDependencies(
        inbox_scanner=MockInboxScanner(_INBOX_FIXTURES),
        detector=HeuristicInviteDetector(),
        prep_generator=HeuristicPrepPacketGenerator(),
        interview_repository=SqlInterviewRepository(session_factory),
        snapshot_store=SqlMasterCvSnapshotStore(session_factory),
        application_repository=SqlApplicationRepository(session_factory),
        validation_log=SqlValidationRunLog(session_factory),
    )
    # The fixture invites arrive late June 2026; scan a window that includes them.
    scan = await run_interview_scan(
        interview_deps,
        settings,
        since=datetime(2026, 6, 1, tzinfo=UTC),
        now=datetime(2026, 7, 2, tzinfo=UTC),
    )
    print(
        f"interviews: {scan.verified} verified into the confirm queue "
        f"({scan.rejected} rejected by provenance verification)"
    )
    print(f"database: {settings.database_url}")


def main() -> int:
    asyncio.run(_seed())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
