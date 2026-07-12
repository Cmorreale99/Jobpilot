"""Audit the whole evidence pipeline for one user — the V4 readiness gate (H8).

``python -m app.tools.audit_pipeline``

Walks the persisted spine offline (zero LLM spend, zero network): source capture,
element coverage, ownership labeling, lifecycle orphans, reviewed-on-stale claims,
the approved-story provenance walk, and normalizer/structurer version consistency
(PIPELINE_HARDENING_PLAN.md §7). Prints the scorecard and records it in
``validation_runs`` (kind ``pipeline_audit``) — a passing row on the live corpus IS
the V4 readiness gate's sign-off artifact. Exit code 0 on pass, 1 on any failure.
"""

from __future__ import annotations

import logging

from app.config import get_settings
from app.db.claim_repository import SqlClaimRepository
from app.db.project_story_repository import SqlProjectStoryRepository
from app.db.session import create_all, create_db_engine, create_session_factory
from app.db.source_capture_store import SqlSourceCaptureStore
from app.db.validation_run_log import SqlValidationRunLog
from app.services.pipeline_audit import run_pipeline_audit


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    settings = get_settings()
    engine = create_db_engine(settings)
    create_all(engine)
    session_factory = create_session_factory(engine)

    scorecard = run_pipeline_audit(
        settings.pipeline_user_id,
        SqlClaimRepository(session_factory),
        SqlSourceCaptureStore(session_factory),
        SqlProjectStoryRepository(session_factory),
        validation_log=SqlValidationRunLog(session_factory),
    )
    for line in scorecard.detail_lines():
        print(line)
    print(f"\npipeline audit: {'PASS' if scorecard.passed else 'FAIL'}")
    print(f"database: {settings.database_url}")
    return 0 if scorecard.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
