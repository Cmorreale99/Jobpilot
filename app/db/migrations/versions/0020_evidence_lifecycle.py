"""evidence lifecycle: is_active + superseded_by_id (hardening H6)

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-11

Supersede, never orphan: when a re-ingest changes a document's chunking, the rows the
fresh chunk set no longer contains are marked inactive — pointing at their overlapping
successor when one is determinable — instead of accumulating as live stale evidence or
being deleted out from under their claim_evidence links. Active rows for a base ref
are, by construction, exactly the current chunking; superseded rows remain visible
history. Legacy rows default active and behave identically until first supersession.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "evidence",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("evidence", sa.Column("superseded_by_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("evidence", "superseded_by_id")
    op.drop_column("evidence", "is_active")
