"""add jobs.canonical_url (V2 M12 — posting links on every card)

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-04

``jobs.url`` keeps the posting exactly as the source gave it; ``canonical_url`` is the
tracking-stripped canonical form, stamped at ingestion when resolvable.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("canonical_url", sa.String(length=1024), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "canonical_url")
