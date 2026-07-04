"""Seed the dev database with the V2 fixture loop — ``python -m app.tools.seed_v2_demo``.

Runs claim extraction over the V2 claims fixtures (mock Drive + GitHub) and the
interview scan over the inbox fixtures (mock scanner), persisting into the configured
``DATABASE_URL``. Zero credentials, zero network — regardless of what the local
``.env`` enables, this tool always uses the mocks (it is a demo seeder, not a live run).

After seeding, the dashboard shows: pending claims in the review queue (one clean, one
coupling-flagged, commit-derived ones missing results) and two verified interviews in
the confirm queue. Idempotent: re-running refreshes unreviewed rows only.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.db.application_repository import SqlApplicationRepository
from app.db.claim_repository import SqlClaimRepository
from app.db.interview_repository import SqlInterviewRepository
from app.db.master_cv_snapshot_store import SqlMasterCvSnapshotStore
from app.db.session import create_all, create_db_engine, create_session_factory
from app.db.validation_run_log import SqlValidationRunLog
from app.domain.interviews import HeuristicInviteDetector, HeuristicPrepPacketGenerator
from app.integrations.mock.drive import MockDriveClient
from app.integrations.mock.github import MockGitHubClient
from app.integrations.mock.inbox import MockInboxScanner
from app.services.claim_extraction import run_claim_extraction
from app.services.interview_scan import InterviewScanDependencies, run_interview_scan

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CLAIMS_FIXTURES = _REPO_ROOT / "tests" / "fixtures" / "claims"
_INBOX_FIXTURES = _REPO_ROOT / "tests" / "fixtures" / "inbox"


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
    report = await run_claim_extraction(
        MockDriveClient(_CLAIMS_FIXTURES / "drive"),
        MockGitHubClient(_CLAIMS_FIXTURES / "github"),
        user_id,
        claim_repo,
        settings,
        validation_log=SqlValidationRunLog(session_factory),
    )
    print(
        f"claims: {len(report.claims)} pending review "
        f"({len(report.flagged)} flagged, {len(report.missing_results)} missing results)"
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
