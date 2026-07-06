"""project_stories + evidence normalizer versioning — V3 Phase 2 story domain

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-06

The V3 story layer's schema (docs/ARCHITECTURE_V3.md §2.2 — the doc numbers this
migration 0012; Phase 0's flag-strip data migration took that slot):

* ``project_stories`` — at most one story per confirmed roster entity
  (``experience_id`` unique). ``review_status`` is the ONLY lifecycle column
  (draft|pending_review|approved|excluded); machine readiness is derived at read
  time and deliberately has no column. Components cite claim ids only
  (``problem_refs``/``actions_json``/``results_json``); ``synthesis_hash`` gives
  re-synthesis the same skip economics as ``experiences.extraction_hash``;
  ``reviewed_at``/``decision_note`` make approval a timestamped human act and
  exclusion a retained decision.
* ``evidence.normalization_version`` — chunk ``#chars=`` span refs are offsets into
  normalizer output, so each row records the normalizer generation that produced its
  text (``NORMALIZATION_VERSION``, ``app/domain/text_normalization.py``). Existing
  rows stay NULL (= written before versioning); no backfill — inventing a version
  for text we can't re-derive would defeat the point.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_stories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("experience_id", sa.Integer(), nullable=False),
        sa.Column("review_status", sa.String(length=16), nullable=False),
        sa.Column("problem_text", sa.Text(), nullable=True),
        sa.Column("problem_refs", sa.JSON(), nullable=True),
        sa.Column("actions_json", sa.JSON(), nullable=True),
        sa.Column("results_json", sa.JSON(), nullable=True),
        sa.Column("synthesis_hash", sa.String(length=64), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("experience_id", name="uq_project_stories_experience"),
    )
    op.create_index("ix_project_stories_user_id", "project_stories", ["user_id"])
    op.add_column("evidence", sa.Column("normalization_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("evidence", "normalization_version")
    op.drop_index("ix_project_stories_user_id", table_name="project_stories")
    op.drop_table("project_stories")
