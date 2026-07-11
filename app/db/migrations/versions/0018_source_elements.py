"""source_elements + version structuring columns (hardening H4)

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-11

Canonical source structure: one row per structural element of a captured version —
verbatim raw spans, heading hierarchy (parent_element_id), document order
(sequence_index), and an explicit per-element disposition. Elements are a pure,
replaceable derivation of the version's immutable raw_text; the version row records
which STRUCTURER_VERSION produced its tree and whether the tree reconciled
(ingestion_status ok/failed — a failed version must not feed downstream synthesis).
Substrate only: nothing consumes these rows until H5 flips chunking/assignment.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_elements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_version_id", sa.Integer(), nullable=False),
        sa.Column("sequence_index", sa.Integer(), nullable=False),
        sa.Column("parent_element_id", sa.Integer(), nullable=True),
        sa.Column("element_type", sa.String(length=32), nullable=False),
        sa.Column("level", sa.Integer(), nullable=True),
        sa.Column("raw_start", sa.Integer(), nullable=False),
        sa.Column("raw_end", sa.Integer(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("extraction_status", sa.String(length=16), nullable=False, default="ok"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.UniqueConstraint("document_version_id", "sequence_index", name="uq_element_version_seq"),
    )
    op.create_index(
        "ix_source_elements_document_version_id", "source_elements", ["document_version_id"]
    )
    op.add_column(
        "source_document_versions", sa.Column("structurer_version", sa.Integer(), nullable=True)
    )
    op.add_column(
        "source_document_versions",
        sa.Column("ingestion_status", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("source_document_versions", "ingestion_status")
    op.drop_column("source_document_versions", "structurer_version")
    op.drop_index("ix_source_elements_document_version_id", table_name="source_elements")
    op.drop_table("source_elements")
