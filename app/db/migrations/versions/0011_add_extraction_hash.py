"""add experiences.extraction_hash — skip re-extracting unchanged evidence

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-05

The fingerprint of the evidence a group was last successfully extracted from. A re-run
whose group fingerprint matches skips the (expensive) LLM extraction entirely — the
queued claims already reflect this evidence. NULL means never extracted (or the last
attempt failed), so the group always runs.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("experiences", sa.Column("extraction_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("experiences", "extraction_hash")
