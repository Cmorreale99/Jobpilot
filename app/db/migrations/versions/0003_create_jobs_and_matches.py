"""create jobs and job_matches

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-01

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=1024), nullable=False),
        sa.Column("company", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("location", sa.String(length=512), nullable=True),
        sa.Column("url", sa.String(length=1024), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("remote", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("source", "external_id", name="uq_jobs_source_external"),
    )

    op.create_table(
        "job_matches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("master_cv_version", sa.Integer(), nullable=False),
        sa.Column("job_source", sa.String(length=64), nullable=False),
        sa.Column("job_external_id", sa.String(length=255), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("matched_terms", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "user_id",
            "master_cv_version",
            "job_source",
            "job_external_id",
            name="uq_job_match_user_version_job",
        ),
    )
    op.create_index("ix_job_matches_user_id", "job_matches", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_job_matches_user_id", table_name="job_matches")
    op.drop_table("job_matches")
    op.drop_table("jobs")
