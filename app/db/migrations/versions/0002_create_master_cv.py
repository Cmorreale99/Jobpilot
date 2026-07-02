"""create master_cv and cv_sources

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-01

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "master_cv",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "version", name="uq_master_cv_user_version"),
    )
    op.create_index("ix_master_cv_user_id", "master_cv", ["user_id"], unique=False)

    op.create_table(
        "cv_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("external_ref", sa.String(length=512), nullable=False),
        sa.Column("title", sa.String(length=1024), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("modified_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "source_type", "external_ref", name="uq_cv_source_user_ref"),
    )
    op.create_index("ix_cv_sources_user_id", "cv_sources", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_cv_sources_user_id", table_name="cv_sources")
    op.drop_table("cv_sources")
    op.drop_index("ix_master_cv_user_id", table_name="master_cv")
    op.drop_table("master_cv")
