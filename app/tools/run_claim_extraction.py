"""Run V2 claim extraction against the CONFIGURED sources — real or mock.

``python -m app.tools.run_claim_extraction``

Uses the same factories as the nightly pipeline: with ``GDRIVE_MCP_ENABLED`` /
``GITHUB_MCP_ENABLED`` true (and the MCP servers running), this reads your real
Drive folder and GitHub repositories, extracts PAR claims (LLM-backed when
``CLAIMS_LLM_EXTRACTION=true``), PAR-validates them, and lands them in the review
queue as ``pending_review`` — nothing becomes canonical without your review.

Idempotent: re-runs refresh unreviewed claims only; approved/rejected rows are
never touched or re-queued.
"""

from __future__ import annotations

import asyncio
import logging

from app.config import get_settings
from app.db.claim_repository import SqlClaimRepository
from app.db.session import create_all, create_db_engine, create_session_factory
from app.db.source_capture_store import SqlSourceCaptureStore
from app.db.validation_run_log import SqlValidationRunLog
from app.integrations.drive_factory import create_drive_client
from app.integrations.github_factory import create_github_client
from app.services.claim_extraction import run_claim_extraction
from app.services.roster import run_roster_assignment


async def _run() -> None:
    settings = get_settings()
    engine = create_db_engine(settings)
    create_all(engine)
    session_factory = create_session_factory(engine)
    repository = SqlClaimRepository(session_factory)
    validation_log = SqlValidationRunLog(session_factory)
    drive_client = create_drive_client(settings)
    github_client = create_github_client(settings)

    # Refresh the chunk assignments first so extraction groups reflect the current
    # sources. Without a confirmed roster, assignment warns and no-ops — and
    # extraction then REFUSES (there is no per-file fallback): run
    # `python -m app.tools.run_roster_detection` and confirm the roster first.
    # Wired like POST /roster/assign: the gather captures raw source text (H2)
    # and the reconciliation pass lands in validation_runs (H6).
    assignment = await run_roster_assignment(
        drive_client,
        github_client,
        settings.pipeline_user_id,
        repository,
        settings,
        capture_store=SqlSourceCaptureStore(session_factory),
        validation_log=validation_log,
    )
    if assignment.chunks:
        print(
            f"roster assignment: {assignment.assigned}/{assignment.chunks} chunks assigned "
            f"({assignment.unassigned} unassigned)"
        )

    report = await run_claim_extraction(
        settings.pipeline_user_id,
        repository,
        settings,
        validation_log=validation_log,
    )
    print(
        f"claims: {len(report.claims)} pending review "
        f"({len(report.flagged)} flagged, {len(report.missing_results)} missing results)"
    )
    print(
        f"gates: {len(report.dropped)} dropped as structurally invalid, "
        f"{len(report.deduped)} dropped as duplicates, "
        f"{len(report.failed_groups)} group(s) failed extraction, "
        f"{len(report.skipped_unchanged)} group(s) skipped (evidence unchanged)"
    )
    print(f"database: {settings.database_url}")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
