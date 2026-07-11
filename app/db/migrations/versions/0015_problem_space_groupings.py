"""problem_space_groupings — recorded partitions (v3.1 Increment 8)

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-11

The drift fix for LLM problem-space detection (live-verified failure mode: eval
re-derived the partition with fresh LLM calls and the model regrouped borderline
problems between passes, flagging a legitimate story as contaminated). One row per
recorded partition, keyed by ``(user_id, fingerprint)`` where the fingerprint hashes
the problem SET plus the detector's version namespace; ``groups_json`` holds the
sanitized groups as normalized problem keys. First write wins — synthesis, eval, and
re-runs over unchanged problems replay the recording (zero LLM spend); a changed
problem set is a new fingerprint and a fresh consultation.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "problem_space_groupings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("groups_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "fingerprint", name="uq_groupings_user_fingerprint"),
    )
    op.create_index("ix_problem_space_groupings_user_id", "problem_space_groupings", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_problem_space_groupings_user_id", table_name="problem_space_groupings")
    op.drop_table("problem_space_groupings")
