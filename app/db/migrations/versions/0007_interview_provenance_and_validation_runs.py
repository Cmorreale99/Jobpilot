"""interview provenance (gmail_message_id + evidence_quote) and validation_runs

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-04

V2 M10: hard interview provenance — ``source_message_id`` becomes ``gmail_message_id``
(same NOT NULL + per-user unique semantics) and ``evidence_quote`` (verbatim trigger
text, verified against the re-fetched message before insert) is added. Existing rows
get an empty quote; they predate verification and stay visibly unverified.

``validation_runs`` records every deterministic verification: PAR validator runs and
interview provenance checks (V3 adds NLI/LLM-judge rows to the same table).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # batch mode: SQLite cannot ALTER COLUMN in place; batch recreates the table and
    # carries the (user_id, message id) unique constraint over to the renamed column.
    with op.batch_alter_table("interviews") as batch:
        batch.alter_column("source_message_id", new_column_name="gmail_message_id")
        batch.add_column(sa.Column("evidence_quote", sa.Text(), nullable=False, server_default=""))

    op.create_table(
        "validation_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("subject_ref", sa.String(length=512), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_validation_runs_user_id", "validation_runs", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_validation_runs_user_id", table_name="validation_runs")
    op.drop_table("validation_runs")
    with op.batch_alter_table("interviews") as batch:
        batch.drop_column("evidence_quote")
        batch.alter_column("gmail_message_id", new_column_name="source_message_id")
