"""Score the current extraction state and summarize the golden set — offline, free.

``python -m app.tools.eval_extraction [--export golden.jsonl]``

Reads the configured database (no LLM calls, no network):

* Slop metrics over the UNREVIEWED queue — what the next review pass will face.
  Fragments/unspecific problems should be zero (the structural gates drop them);
  cross-project links MUST be zero (the roster boundary makes them unrepresentable).
* Golden-set summary over the decided claims — every retained approve/reject/edit is
  a labeled example. ``--export`` writes them as JSONL for prompt tuning or a future
  judge step.

The same metrics are recorded automatically per extraction run
(``validation_runs`` kind ``extraction_eval``), so changes to the extractor or its
prompts get a scorecard trend, not vibes.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from app.config import get_settings
from app.db.claim_repository import SqlClaimRepository
from app.db.session import create_all, create_db_engine, create_session_factory
from app.domain.claims import ClaimStatus
from app.domain.evaluation import (
    compute_slop_metrics,
    golden_examples,
    summarize_golden_set,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--export",
        type=Path,
        default=None,
        help="write the golden-set examples (decided claims) to this JSONL file",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING)
    settings = get_settings()
    engine = create_db_engine(settings)
    create_all(engine)
    repository = SqlClaimRepository(create_session_factory(engine))
    user_id = settings.pipeline_user_id

    pending = repository.list_claims(user_id, status=ClaimStatus.PENDING_REVIEW)
    metrics = compute_slop_metrics(pending, repository.get_evidence)
    print(f"slop metrics over the unreviewed queue ({user_id}):")
    for line in metrics.detail_lines():
        print(f"  {line}")
    print(f"  boundary_clean={metrics.boundary_clean}  <- must stay True")

    decided = repository.list_claims(user_id)
    summary = summarize_golden_set(decided)
    print(
        f"golden set: {summary.total} labeled decisions "
        f"({summary.approved} approved, {summary.rejected} rejected, "
        f"{summary.attested} with attested results)"
    )
    for reason, count in summary.top_rejection_reasons:
        print(f"  rejection x{count}: {reason}")
    if summary.total == 0:
        print("  (empty - approve/reject claims in the review queue to grow it)")

    if args.export is not None:
        examples = golden_examples(decided, repository.get_evidence)
        with args.export.open("w", encoding="utf-8") as handle:
            for example in examples:
                handle.write(json.dumps(example, ensure_ascii=False) + "\n")
        print(f"exported {len(examples)} golden examples to {args.export}")

    print(f"database: {settings.database_url}")
    return 0 if metrics.boundary_clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
