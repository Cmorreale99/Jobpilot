"""evidence.assignment_method + assigned_at — human pins (hardening H1)

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-11

The evidence table stored a chunk's project assignment as a bare FK with no record
of HOW it was decided, so a human correction (POST /roster/evidence/{id}/assign) was
bitwise indistinguishable from a machine guess and the next assignment re-run
silently overwrote it (PIPELINE_HARDENING_PLAN.md F2). ``assignment_method`` labels
every assignment (heuristic|llm|readme_ref|repo_ref|human|section); ``human`` rows
are pinned — ``run_roster_assignment`` skips them. NULL = legacy/unlabeled rows,
treated as machine (re-assignable), so existing data behaves exactly as before.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("evidence", sa.Column("assignment_method", sa.String(length=16), nullable=True))
    op.add_column("evidence", sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("evidence", "assigned_at")
    op.drop_column("evidence", "assignment_method")
