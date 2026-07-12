"""Zero-cost ingestion preflight + candidate reconciliation.

``python -m app.tools.ingestion_preflight [--reconcile]``

Phase 1 (always): enumerate the complete captured source universe (H2 raw layer —
no network, no refetch, no LLM anywhere on this path), persist the authoritative
manifest JSON, and print the preflight summary ending in the explicit
``PAPER_RECOMMENDER_SYSTEM_PRESENT=true|false`` acceptance line. If any required
project is absent from the manifest, exit 1 — ingestion must not run.

``--reconcile`` (Phases 3-4): run the current structurer in memory over every
manifested object's cached raw text (an isolated candidate generation — canonical
tables untouched), checkpointing per object, and print the deterministic
raw-to-extracted reconciliation ledger. Exit 1 unless every metric is 100.000%.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from app.config import get_settings
from app.db.claim_repository import SqlClaimRepository
from app.db.session import create_db_engine, create_session_factory
from app.db.source_capture_store import SqlSourceCaptureStore
from app.services.ingestion_preflight import (
    build_source_manifest,
    format_preflight_summary,
    format_reconciliation_summary,
    required_projects_present,
    run_candidate_reconciliation,
)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    args = argv if argv is not None else sys.argv[1:]
    reconcile = "--reconcile" in args

    settings = get_settings()
    session_factory = create_session_factory(create_db_engine(settings))
    capture_store = SqlSourceCaptureStore(session_factory)
    repository = SqlClaimRepository(session_factory)
    user_id = settings.pipeline_user_id

    out_dir = Path(settings.artifacts_dir) / "ingestion_preflight"
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = build_source_manifest(user_id, capture_store, repository)
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest.to_dict(), indent=1), encoding="utf-8"
    )
    print(format_preflight_summary(manifest))
    print(f"manifest: {out_dir / 'manifest.json'}")

    if not required_projects_present(manifest):
        print("PREFLIGHT FAILED: a required project is not present in the manifest.")
        print("Do not run ingestion. Fix discovery first.")
        return 1

    if not reconcile:
        return 0

    run = run_candidate_reconciliation(
        user_id, capture_store, manifest, checkpoint_path=out_dir / "ledger.json"
    )
    print(format_reconciliation_summary(run))
    print(f"ledger: {out_dir / 'ledger.json'}")
    return 0 if run.report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
