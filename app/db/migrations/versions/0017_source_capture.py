"""source_documents + source_document_versions; drop dead cv_sources (hardening H2)

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-11

Canonical source capture — point of no return #1. Every gathered source's as-received
(pre-normalization) text becomes durable in ``source_document_versions``; identity and
latest metadata live in ``source_documents``. Raw payload rows are immutable after
insert (only ``is_active`` moves), so every downstream transform is a recomputable
derivation of a stored original (PIPELINE_HARDENING_PLAN.md F1).

``cv_sources`` is dropped: it is dead V1 schema — no code path has ever written to it
(verified: zero writers repo-wide). The drop is guarded: any rows present mean that
finding was wrong for this database, and the migration refuses rather than destroys.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_ref", sa.String(length=512), nullable=False),
        sa.Column("title", sa.String(length=1024), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=True),
        sa.Column("modified_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("user_id", "source_type", "source_ref", name="uq_source_doc_user_ref"),
    )
    op.create_index("ix_source_documents_user_id", "source_documents", ["user_id"])

    op.create_table(
        "source_document_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("extractor", sa.String(length=128), nullable=False),
        sa.Column("normalization_version", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, default=True),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("document_id", "content_hash", name="uq_source_version_doc_hash"),
    )
    op.create_index(
        "ix_source_document_versions_document_id", "source_document_versions", ["document_id"]
    )

    # Drop the dead V1 table — guarded: it never had a writer, so any rows mean the
    # audit finding was wrong for THIS database and we must not destroy data.
    count = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM cv_sources")).scalar()
    if count:
        raise RuntimeError(
            f"cv_sources holds {count} row(s) — expected the table to be empty (no writer "
            "exists). Refusing to drop it; export the rows and re-run."
        )
    op.drop_index("ix_cv_sources_user_id", table_name="cv_sources")
    op.drop_table("cv_sources")


def downgrade() -> None:
    # Recreate cv_sources exactly as migration 0002 defined it (it was empty).
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
    op.drop_index("ix_source_document_versions_document_id", table_name="source_document_versions")
    op.drop_table("source_document_versions")
    op.drop_index("ix_source_documents_user_id", table_name="source_documents")
    op.drop_table("source_documents")
