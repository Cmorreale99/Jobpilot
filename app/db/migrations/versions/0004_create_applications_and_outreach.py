"""create applications and outreach

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-02

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "applications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("job_source", sa.String(length=64), nullable=False),
        sa.Column("job_external_id", sa.String(length=255), nullable=False),
        sa.Column("job_title", sa.String(length=1024), nullable=False),
        sa.Column("job_company", sa.String(length=512), nullable=False),
        sa.Column("master_cv_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("materials_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "user_id", "job_source", "job_external_id", name="uq_applications_user_job"
        ),
    )
    op.create_index("ix_applications_user_id", "applications", ["user_id"], unique=False)

    op.create_table(
        "outreach",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("contact_name", sa.String(length=512), nullable=True),
        sa.Column("contact_title", sa.String(length=512), nullable=True),
        sa.Column("contact_email", sa.String(length=320), nullable=True),
        sa.Column("contact_source", sa.String(length=512), nullable=True),
        sa.Column("subject", sa.String(length=1024), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("application_id", name="uq_outreach_application"),
    )
    op.create_index("ix_outreach_user_id", "outreach", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_outreach_user_id", table_name="outreach")
    op.drop_table("outreach")
    op.drop_index("ix_applications_user_id", table_name="applications")
    op.drop_table("applications")
