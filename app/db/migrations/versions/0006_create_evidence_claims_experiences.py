"""create evidence, claims, claim_evidence, experiences (V2 M8)

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-03

Schema per jobpilot_v2_outline_v2.1 §1 with the v2.2 delta applied:
``claims.problem_inefficiency``, ``experiences.section``, ``experiences.sort_order``,
and ``claim_evidence.outcome_quote`` (required when field='result'; enforced in the
PAR validator — source types and field values deliberately carry no CHECK constraint).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "experiences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("subtitle", sa.String(length=512), nullable=True),
        sa.Column("dates", sa.String(length=128), nullable=True),
        sa.Column(
            "section",
            sa.String(length=32),
            nullable=False,
            server_default="professional_experience",
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("user_id", "name", name="uq_experiences_user_name"),
    )
    op.create_index("ix_experiences_user_id", "experiences", ["user_id"], unique=False)

    op.create_table(
        "evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_ref", sa.String(length=512), nullable=False),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("user_id", "source_type", "source_ref", name="uq_evidence_user_source"),
    )
    op.create_index("ix_evidence_user_id", "evidence", ["user_id"], unique=False)

    op.create_table(
        "claims",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("experience_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("problem_text", sa.Text(), nullable=True),
        sa.Column("problem_cost_dimension", sa.String(length=16), nullable=True),
        sa.Column("problem_inefficiency", sa.String(length=32), nullable=True),
        sa.Column("action_text", sa.Text(), nullable=False),
        sa.Column("action_tools", sa.JSON(), nullable=False),
        sa.Column("result_text", sa.Text(), nullable=True),
        sa.Column("result_kind", sa.String(length=32), nullable=False),
        sa.Column("result_status", sa.String(length=16), nullable=False),
        sa.Column("result_metric_json", sa.JSON(), nullable=True),
        sa.Column("validation_flags", sa.JSON(), nullable=False),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_claims_user_id", "claims", ["user_id"], unique=False)
    op.create_index("ix_claims_experience_id", "claims", ["experience_id"], unique=False)

    op.create_table(
        "claim_evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("claim_id", sa.Integer(), nullable=False),
        sa.Column("evidence_id", sa.Integer(), nullable=False),
        sa.Column("field", sa.String(length=16), nullable=False),
        sa.Column("outcome_quote", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "claim_id", "evidence_id", "field", name="uq_claim_evidence_claim_evidence_field"
        ),
    )
    op.create_index("ix_claim_evidence_claim_id", "claim_evidence", ["claim_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_claim_evidence_claim_id", table_name="claim_evidence")
    op.drop_table("claim_evidence")
    op.drop_index("ix_claims_experience_id", table_name="claims")
    op.drop_index("ix_claims_user_id", table_name="claims")
    op.drop_table("claims")
    op.drop_index("ix_evidence_user_id", table_name="evidence")
    op.drop_table("evidence")
    op.drop_index("ix_experiences_user_id", table_name="experiences")
    op.drop_table("experiences")
