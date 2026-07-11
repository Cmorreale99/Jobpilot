"""evidence structure linkage: element_id, sequence_index, section_path (hardening H5)

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-11

Section-scoped ownership: an evidence chunk now records WHICH structural element it
was cut from (element_id -> source_elements), its document order (sequence_index — a
column, not a parse of the source_ref), and the heading trail governing it
(section_path, e.g. "Cooper.ai — data engineering > FedEx migration"). All three are
nullable: legacy rows and structureless sources (commits, plain fragments) simply
carry NULL and behave as before. Chunk identity itself still lives in the span ref.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("evidence", sa.Column("element_id", sa.Integer(), nullable=True))
    op.add_column("evidence", sa.Column("sequence_index", sa.Integer(), nullable=True))
    op.add_column("evidence", sa.Column("section_path", sa.Text(), nullable=True))
    op.create_index("ix_evidence_element_id", "evidence", ["element_id"])


def downgrade() -> None:
    op.drop_index("ix_evidence_element_id", table_name="evidence")
    op.drop_column("evidence", "section_path")
    op.drop_column("evidence", "sequence_index")
    op.drop_column("evidence", "element_id")
