"""roster layer (V2 M13) — experiences become real entities, evidence gets assignment

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-04

Experiences gain the roster columns: ``kind`` (employer_role|project), ``status``
(proposed|confirmed|merged|discarded), ``aliases`` (JSON list), ``merged_into_id``.
Existing rows default to ``confirmed`` so pre-roster data keeps flowing; the roster
review is where filename-shaped legacy entities get merged/renamed/discarded.

Evidence gains ``experience_id`` — the chunk's project assignment. Chunk char spans
ride in ``source_ref`` (``<base>#chars=<start>-<end>``), so no schema change there.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "experiences",
        sa.Column("kind", sa.String(length=32), nullable=False, server_default="project"),
    )
    op.add_column(
        "experiences",
        sa.Column("status", sa.String(length=16), nullable=False, server_default="confirmed"),
    )
    op.add_column("experiences", sa.Column("aliases", sa.JSON(), nullable=True))
    op.add_column("experiences", sa.Column("merged_into_id", sa.Integer(), nullable=True))
    op.add_column("evidence", sa.Column("experience_id", sa.Integer(), nullable=True))
    op.create_index("ix_evidence_experience_id", "evidence", ["experience_id"])


def downgrade() -> None:
    op.drop_index("ix_evidence_experience_id", table_name="evidence")
    op.drop_column("evidence", "experience_id")
    op.drop_column("experiences", "merged_into_id")
    op.drop_column("experiences", "aliases")
    op.drop_column("experiences", "status")
    op.drop_column("experiences", "kind")
