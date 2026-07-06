"""In-memory :class:`ProjectStoryRepository` — the mock-first default for dev/tests.

Implements the shared semantics documented on the protocol
(``app/domain/project_story.py``): one story per experience, machine drafts replace
only machine drafts (a human-decided story raises :class:`StoryReplaceError`),
transitions follow the story state machine, and ``invalidate_story`` is the only path
back to ``draft`` — explicit, with the prior decision retained in ``decision_note``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from app.domain.project_story import (
    ProjectStory,
    StoryContent,
    StoryReplaceError,
    StoryReviewStatus,
    validate_story_transition,
)

_DECIDED = (StoryReviewStatus.APPROVED, StoryReviewStatus.EXCLUDED)


def invalidation_note(story: ProjectStory, reason: str) -> str:
    """The retained record of an invalidation (prior decision never silently lost)."""
    note = f"invalidated: {reason}"
    if story.review_status in _DECIDED:
        stamp = story.reviewed_at.isoformat() if story.reviewed_at else "unknown time"
        note += f" (previously {story.review_status.value} at {stamp})"
    return note


class InMemoryProjectStoryRepository:
    """Dict-backed story persistence with the same semantics as the SQL version."""

    def __init__(self) -> None:
        self._stories: dict[int, ProjectStory] = {}
        self._next_id = 1

    def upsert_draft(self, user_id: str, experience_id: int, content: StoryContent) -> ProjectStory:
        existing = self.get_story_for_experience(user_id, experience_id)
        if existing is not None:
            if existing.review_status in _DECIDED:
                raise StoryReplaceError(
                    f"story {existing.id} is {existing.review_status} — a human decision "
                    "is never replaced by re-synthesis; invalidate it explicitly first"
                )
            updated = replace(
                existing,
                content=content,
                review_status=StoryReviewStatus.DRAFT,
                reviewed_at=None,
                decision_note=None,
            )
            self._stories[existing.id] = updated
            return updated
        story = ProjectStory(
            id=self._next_id,
            user_id=user_id,
            experience_id=experience_id,
            review_status=StoryReviewStatus.DRAFT,
            content=content,
            created_at=datetime.now(tz=UTC),
        )
        self._stories[story.id] = story
        self._next_id += 1
        return story

    def get_story(self, story_id: int) -> ProjectStory | None:
        return self._stories.get(story_id)

    def get_story_for_experience(self, user_id: str, experience_id: int) -> ProjectStory | None:
        return next(
            (
                s
                for s in self._stories.values()
                if s.user_id == user_id and s.experience_id == experience_id
            ),
            None,
        )

    def list_stories(
        self, user_id: str, status: StoryReviewStatus | None = None
    ) -> list[ProjectStory]:
        rows = [
            s
            for s in self._stories.values()
            if s.user_id == user_id and (status is None or s.review_status is status)
        ]
        return sorted(rows, key=lambda s: s.id)

    def transition_story(
        self,
        story_id: int,
        new_status: StoryReviewStatus,
        *,
        decision_note: str | None = None,
        reviewed_at: datetime | None = None,
    ) -> ProjectStory:
        story = self._stories.get(story_id)
        if story is None:
            raise LookupError(f"no story with id {story_id}")
        validate_story_transition(story.review_status, new_status, decision_note=decision_note)
        stamped = (
            (reviewed_at or datetime.now(tz=UTC)) if new_status in _DECIDED else story.reviewed_at
        )
        updated = replace(
            story,
            review_status=new_status,
            decision_note=decision_note if decision_note is not None else story.decision_note,
            reviewed_at=stamped,
        )
        self._stories[story_id] = updated
        return updated

    def invalidate_story(self, experience_id: int, *, reason: str) -> ProjectStory | None:
        story = next((s for s in self._stories.values() if s.experience_id == experience_id), None)
        if story is None:
            return None
        updated = replace(
            story,
            review_status=StoryReviewStatus.DRAFT,
            reviewed_at=None,
            decision_note=invalidation_note(story, reason),
        )
        self._stories[story.id] = updated
        return updated
