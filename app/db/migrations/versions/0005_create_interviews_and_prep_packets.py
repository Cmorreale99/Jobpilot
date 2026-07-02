"""create interviews and prep_packets

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-02

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "interviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("source_message_id", sa.String(length=255), nullable=False),
        sa.Column("company", sa.String(length=512), nullable=False),
        sa.Column("job_title", sa.String(length=1024), nullable=True),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "detected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("user_id", "source_message_id", name="uq_interviews_user_message"),
    )
    op.create_index("ix_interviews_user_id", "interviews", ["user_id"], unique=False)

    op.create_table(
        "prep_packets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("interview_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("interview_id", name="uq_prep_packets_interview"),
    )
    op.create_index("ix_prep_packets_interview_id", "prep_packets", ["interview_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_prep_packets_interview_id", table_name="prep_packets")
    op.drop_table("prep_packets")
    op.drop_index("ix_interviews_user_id", table_name="interviews")
    op.drop_table("interviews")
