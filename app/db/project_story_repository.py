"""SQLAlchemy-backed :class:`ProjectStoryRepository`.

Same semantics as the in-memory implementation (the protocol in
``app/domain/project_story.py`` documents them): one story per
(experience, problem space), machine drafts replace only machine drafts of the
same space, human decisions are never overwritten by an upsert (or removed by
``delete_draft``), and ``invalidate_story`` is the sole explicit path back to
``draft`` — it invalidates every one of the entity's stories.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ProjectStoryRow
from app.domain.project_story import (
    ProjectStory,
    StoryAction,
    StoryContent,
    StoryReplaceError,
    StoryResult,
    StoryReviewStatus,
    validate_story_transition,
)
from app.services.project_story_repository import invalidation_note, stamped_content

_DECIDED = (StoryReviewStatus.APPROVED.value, StoryReviewStatus.EXCLUDED.value)


def _actions_json(content: StoryContent) -> list[dict[str, Any]]:
    return [
        {
            "component_id": action.component_id,
            "summary": action.summary,
            "tools": list(action.tools),
            "claim_ids": list(action.claim_ids),
        }
        for action in content.actions
    ]


def _results_json(content: StoryContent) -> list[dict[str, Any]]:
    return [
        {
            "component_id": result.component_id,
            "text": result.text,
            "outcome_quote": result.outcome_quote,
            "claim_ids": list(result.claim_ids),
        }
        for result in content.results
    ]


def _to_story(row: ProjectStoryRow) -> ProjectStory:
    content = StoryContent(
        problem_text=row.problem_text,
        problem_refs=tuple(row.problem_refs or []),
        actions=tuple(
            StoryAction(
                component_id=entry["component_id"],
                summary=entry["summary"],
                claim_ids=tuple(entry.get("claim_ids", [])),
                tools=tuple(entry.get("tools", [])),
            )
            for entry in (row.actions_json or [])
        ),
        results=tuple(
            StoryResult(
                component_id=entry["component_id"],
                text=entry["text"],
                claim_ids=tuple(entry.get("claim_ids", [])),
                outcome_quote=entry.get("outcome_quote"),
            )
            for entry in (row.results_json or [])
        ),
        synthesis_hash=row.synthesis_hash,
        problem_space_id=row.problem_space_id,
        problem_space_label=row.problem_space_label,
        problem_space_scope=row.problem_space_scope,
        selected_action_id=row.selected_action_id,
        selected_result_id=row.selected_result_id,
        bundle_status=row.bundle_status,
    )
    return ProjectStory(
        id=row.id,
        user_id=row.user_id,
        experience_id=row.experience_id,
        review_status=StoryReviewStatus(row.review_status),
        content=content,
        reviewed_at=row.reviewed_at,
        decision_note=row.decision_note,
        created_at=row.created_at,
    )


def _write_content(row: ProjectStoryRow, content: StoryContent) -> None:
    row.problem_text = content.problem_text
    row.problem_refs = list(content.problem_refs)
    row.actions_json = _actions_json(content)
    row.results_json = _results_json(content)
    row.synthesis_hash = content.synthesis_hash
    row.problem_space_id = content.problem_space_id or ""
    row.problem_space_label = content.problem_space_label
    row.problem_space_scope = content.problem_space_scope
    row.selected_action_id = content.selected_action_id
    row.selected_result_id = content.selected_result_id
    row.bundle_status = content.bundle_status or ""


class SqlProjectStoryRepository:
    """Project-story persistence over a SQLAlchemy session factory."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def upsert_draft(self, user_id: str, experience_id: int, content: StoryContent) -> ProjectStory:
        content = stamped_content(content)
        with self._session_factory() as session:
            row = session.scalar(
                select(ProjectStoryRow).where(
                    ProjectStoryRow.user_id == user_id,
                    ProjectStoryRow.experience_id == experience_id,
                    ProjectStoryRow.problem_space_id == content.problem_space_id,
                )
            )
            if row is not None:
                if row.review_status in _DECIDED:
                    raise StoryReplaceError(
                        f"story {row.id} is {row.review_status} — a human decision is "
                        "never replaced by re-synthesis; invalidate it explicitly first"
                    )
                _write_content(row, content)
                row.review_status = StoryReviewStatus.DRAFT.value
                row.reviewed_at = None
                row.decision_note = None
            else:
                row = ProjectStoryRow(
                    user_id=user_id,
                    experience_id=experience_id,
                    review_status=StoryReviewStatus.DRAFT.value,
                )
                _write_content(row, content)
                session.add(row)
            session.commit()
            session.refresh(row)
            return _to_story(row)

    def get_story(self, story_id: int) -> ProjectStory | None:
        with self._session_factory() as session:
            row = session.get(ProjectStoryRow, story_id)
            return _to_story(row) if row is not None else None

    def get_story_for_experience(self, user_id: str, experience_id: int) -> ProjectStory | None:
        """The entity's first story by id — a convenience for single-space entities."""
        stories = self.list_stories_for_experience(user_id, experience_id)
        return stories[0] if stories else None

    def get_story_for_space(
        self, user_id: str, experience_id: int, problem_space_id: str
    ) -> ProjectStory | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(ProjectStoryRow).where(
                    ProjectStoryRow.user_id == user_id,
                    ProjectStoryRow.experience_id == experience_id,
                    ProjectStoryRow.problem_space_id == problem_space_id,
                )
            )
            return _to_story(row) if row is not None else None

    def list_stories_for_experience(self, user_id: str, experience_id: int) -> list[ProjectStory]:
        with self._session_factory() as session:
            query = (
                select(ProjectStoryRow)
                .where(
                    ProjectStoryRow.user_id == user_id,
                    ProjectStoryRow.experience_id == experience_id,
                )
                .order_by(ProjectStoryRow.id)
            )
            return [_to_story(row) for row in session.scalars(query)]

    def list_stories(
        self, user_id: str, status: StoryReviewStatus | None = None
    ) -> list[ProjectStory]:
        with self._session_factory() as session:
            query = (
                select(ProjectStoryRow)
                .where(ProjectStoryRow.user_id == user_id)
                .order_by(ProjectStoryRow.id)
            )
            if status is not None:
                query = query.where(ProjectStoryRow.review_status == status.value)
            return [_to_story(row) for row in session.scalars(query)]

    def transition_story(
        self,
        story_id: int,
        new_status: StoryReviewStatus,
        *,
        decision_note: str | None = None,
        reviewed_at: datetime | None = None,
    ) -> ProjectStory:
        with self._session_factory() as session:
            row = session.get(ProjectStoryRow, story_id)
            if row is None:
                raise LookupError(f"no story with id {story_id}")
            validate_story_transition(
                StoryReviewStatus(row.review_status), new_status, decision_note=decision_note
            )
            row.review_status = new_status.value
            if decision_note is not None:
                row.decision_note = decision_note
            if new_status.value in _DECIDED:
                row.reviewed_at = reviewed_at or datetime.now(tz=UTC)
            session.commit()
            session.refresh(row)
            return _to_story(row)

    def invalidate_story(self, experience_id: int, *, reason: str) -> list[ProjectStory]:
        with self._session_factory() as session:
            rows = list(
                session.scalars(
                    select(ProjectStoryRow)
                    .where(ProjectStoryRow.experience_id == experience_id)
                    .order_by(ProjectStoryRow.id)
                )
            )
            invalidated: list[ProjectStory] = []
            for row in rows:
                row.decision_note = invalidation_note(_to_story(row), reason)
                row.review_status = StoryReviewStatus.DRAFT.value
                row.reviewed_at = None
            session.commit()
            for row in rows:
                session.refresh(row)
                invalidated.append(_to_story(row))
            return invalidated

    def delete_draft(self, story_id: int) -> None:
        with self._session_factory() as session:
            row = session.get(ProjectStoryRow, story_id)
            if row is None:
                return
            if row.review_status in _DECIDED:
                raise StoryReplaceError(
                    f"story {story_id} is {row.review_status} — a human decision is never "
                    "deleted by stale-space cleanup; invalidate it explicitly first"
                )
            session.delete(row)
            session.commit()
