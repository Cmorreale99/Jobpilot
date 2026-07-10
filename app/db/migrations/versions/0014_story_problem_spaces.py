"""project_stories per problem space — v3.1 Increment 3

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-10

Generalizes the story layer from one-story-per-entity to one story per
(entity, problem space) (docs/research/ARCHITECTURE_V3.1.md, Increment 3):

* ``problem_space_id`` — the detected space's stable content-hash id, or the
  entity's leftover space (``ps-leftover``) for problem-less stories.
* ``problem_space_label`` / ``problem_space_scope`` — the space verbatim (label is
  its defining problem text; scope the pain-point tag summary).
* ``selected_action_id`` / ``selected_result_id`` / ``bundle_status`` — the v3.1
  selection state (Increment 4 stamps selections; detection only ever derives
  ``requires_user_selection`` / ``missing_result``).
* The unique constraint moves from ``experience_id`` to
  ``(experience_id, problem_space_id)``.

Backfill: every existing v3 row gets the SAME space id detection derives for a
single-problem space — ``"ps-" + sha256(normalized problem_text)[:12]`` (the
``component_id`` scheme) — so post-upgrade re-synthesis upserts over the backfilled
row instead of duplicating it; a row with no problem is the entity's leftover space.
``bundle_status`` backfills from result presence. Scope stays NULL (it summarizes
lexicon tags the migration must not re-derive).

Downgrade drops the new columns and restores the per-entity unique constraint; it
fails (deliberately) if multiple spaces per entity already exist — collapsing them
would silently discard stories.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEFTOVER_PROBLEM_SPACE_ID = "ps-leftover"


def _space_id(problem_text: str | None) -> str:
    """Mirror ``component_id("ps", text)`` / ``derive_problem_space_id`` exactly."""
    stripped = (problem_text or "").strip()
    if not stripped:
        return LEFTOVER_PROBLEM_SPACE_ID
    normalized = " ".join(stripped.split()).casefold()
    return "ps-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def _has_results(results_json: object) -> bool:
    if isinstance(results_json, str):
        try:
            results_json = json.loads(results_json)
        except ValueError:
            return False
    return bool(results_json)


def upgrade() -> None:
    op.add_column(
        "project_stories", sa.Column("problem_space_id", sa.String(length=64), nullable=True)
    )
    op.add_column("project_stories", sa.Column("problem_space_label", sa.Text(), nullable=True))
    op.add_column("project_stories", sa.Column("problem_space_scope", sa.Text(), nullable=True))
    op.add_column(
        "project_stories", sa.Column("selected_action_id", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "project_stories", sa.Column("selected_result_id", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "project_stories", sa.Column("bundle_status", sa.String(length=32), nullable=True)
    )

    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, problem_text, results_json FROM project_stories")
    ).fetchall()
    for row_id, problem_text, results_json in rows:
        connection.execute(
            sa.text(
                "UPDATE project_stories SET problem_space_id = :space_id, "
                "problem_space_label = :label, bundle_status = :status WHERE id = :id"
            ),
            {
                "space_id": _space_id(problem_text),
                "label": (problem_text or "").strip() or None,
                "status": (
                    "requires_user_selection" if _has_results(results_json) else "missing_result"
                ),
                "id": row_id,
            },
        )

    with op.batch_alter_table("project_stories") as batch:
        batch.alter_column("problem_space_id", existing_type=sa.String(64), nullable=False)
        batch.alter_column("bundle_status", existing_type=sa.String(32), nullable=False)
        batch.drop_constraint("uq_project_stories_experience", type_="unique")
        batch.create_unique_constraint(
            "uq_project_stories_experience_space", ["experience_id", "problem_space_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("project_stories") as batch:
        batch.drop_constraint("uq_project_stories_experience_space", type_="unique")
        batch.create_unique_constraint("uq_project_stories_experience", ["experience_id"])
        batch.drop_column("bundle_status")
        batch.drop_column("selected_result_id")
        batch.drop_column("selected_action_id")
        batch.drop_column("problem_space_scope")
        batch.drop_column("problem_space_label")
        batch.drop_column("problem_space_id")
