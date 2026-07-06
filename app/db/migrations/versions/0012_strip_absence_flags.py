"""strip absence flags from unreviewed claims — V3 Phase 0 flag split

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-05

Phase 0 of docs/ARCHITECTURE_V3.md splits the validator families: ABSENCE codes
(problem_missing, problem_not_pain_point — the evidence honestly states no pain
point) are readiness data, not claim defects, and stop persisting as
``validation_flags``; INTEGRITY flags keep blocking approve-as-is. This data
migration applies the split to already-extracted rows: absence flags are removed
from claims no human has decided on (extracted / pending_review). Reviewed rows
are never touched — approve/reject decisions and whatever they were made on are
golden-set data.

Data-only; no schema change. Irreversible by design: the absence fact stays
recomputable from the claim itself (``problem_text`` is empty).
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Keep in sync with app.domain.par_validation.ABSENCE_CODES (frozen here on purpose:
# a migration describes what happened, not what the code says later).
_ABSENCE_CODES = ("problem_missing", "problem_not_pain_point")
_UNREVIEWED = ("extracted", "pending_review")


def _is_absence(flag: str) -> bool:
    code = flag.split(":", 1)[0].strip()
    return code in _ABSENCE_CODES


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, validation_flags FROM claims "
            "WHERE status IN :statuses AND validation_flags IS NOT NULL"
        ).bindparams(sa.bindparam("statuses", expanding=True)),
        {"statuses": list(_UNREVIEWED)},
    ).all()
    for claim_id, raw_flags in rows:
        flags = raw_flags if isinstance(raw_flags, list) else json.loads(raw_flags or "[]")
        kept = [flag for flag in flags if not _is_absence(flag)]
        if kept != flags:
            connection.execute(
                sa.text("UPDATE claims SET validation_flags = :flags WHERE id = :id"),
                {"flags": json.dumps(kept), "id": claim_id},
            )


def downgrade() -> None:
    # Deliberately a no-op: the removed flags are derived data (problem_text is empty)
    # and re-materializing them would re-block the approve gate the split unblocked.
    pass
