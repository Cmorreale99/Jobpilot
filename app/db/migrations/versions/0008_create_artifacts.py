"""create artifacts (V2 M11 — rendered Master CV docx tracking)

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-04

The ``artifacts`` table from the v2.0 outline, kept in V2 with only the
``master_cv_docx`` kind populated. Upserts dedupe on
``(user_id, kind, master_cv_version)`` — re-rendering a version refreshes the row.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("master_cv_version", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "user_id", "kind", "master_cv_version", name="uq_artifacts_user_kind_version"
        ),
    )
    op.create_index("ix_artifacts_user_id", "artifacts", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_artifacts_user_id", table_name="artifacts")
    op.drop_table("artifacts")
