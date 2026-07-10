"""Project-story repository contract tests, run against both implementations.

The load-bearing semantics (docs/ARCHITECTURE_V3.md §2.1, v3.1 Increment 3): one
story per (entity, problem space); machine drafts replace only machine drafts of the
same space; a human decision (approved/excluded) is NEVER replaced by re-synthesis
(or deleted by stale-space cleanup); exclusion requires a retained reason; approval
is a timestamped act; and invalidation — the roster-merge/discard cascade and the
explicit un-approve — is the only way back to ``draft``, with the prior decision
recorded, across every one of the entity's stories.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import sqlalchemy as sa
from app.db.base import Base
from app.db.claim_repository import SqlClaimRepository
from app.db.project_story_repository import SqlProjectStoryRepository
from app.db.session import create_session_factory
from app.domain.applications import InvalidTransitionError
from app.domain.claims import SOURCE_GITHUB_COMMIT, ClaimRepository, EvidenceChunk
from app.domain.project_story import (
    LEFTOVER_PROBLEM_SPACE_ID,
    ExclusionReasonRequiredError,
    ProjectStoryRepository,
    StoryAction,
    StoryContent,
    StoryReplaceError,
    StoryResult,
    StoryReviewStatus,
    component_id,
    derive_problem_space_id,
)
from app.domain.text_normalization import NORMALIZATION_VERSION
from app.services.claim_repository import InMemoryClaimRepository
from app.services.project_story_repository import (
    InMemoryProjectStoryRepository,
    stamped_content,
)


def _sql_repo(tmp_path: Path) -> SqlProjectStoryRepository:
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'stories.db'}")
    Base.metadata.create_all(engine)
    return SqlProjectStoryRepository(create_session_factory(engine))


@pytest.fixture(params=["in_memory", "sql"])
def repo(request: pytest.FixtureRequest, tmp_path: Path) -> ProjectStoryRepository:
    if request.param == "in_memory":
        return InMemoryProjectStoryRepository()
    return _sql_repo(tmp_path)


_CONTENT = StoryContent(
    problem_text="The permit team spent 6 hours a week reconciling exports by hand",
    problem_refs=(11,),
    actions=(
        StoryAction(
            component_id=component_id("a", "Built the permit ingestion step"),
            summary="Built the permit ingestion step in Python with Pandas",
            claim_ids=(11, 12),
            tools=("Python", "Pandas"),
        ),
    ),
    results=(
        StoryResult(
            component_id=component_id("r", "Reduced permit export time"),
            text="Reduced permit export time from 10 hours to 2 hours",
            claim_ids=(11,),
            outcome_quote="Reduced permit export time from 10 hours to 2 hours",
        ),
    ),
    synthesis_hash="abc123",
)


def test_upsert_creates_a_draft_and_round_trips_content(repo: ProjectStoryRepository) -> None:
    story = repo.upsert_draft("u1", 7, _CONTENT)
    assert story.review_status is StoryReviewStatus.DRAFT
    assert story.reviewed_at is None

    loaded = repo.get_story(story.id)
    assert loaded is not None
    # Persisted content is the stamped form: the space key and bundle status are
    # always materialized, however the caller filled the optional fields.
    assert loaded.content == stamped_content(_CONTENT)
    assert loaded.problem_space_id == derive_problem_space_id(_CONTENT)
    assert loaded.content.bundle_status == "requires_user_selection"
    assert repo.get_story_for_experience("u1", 7) == loaded
    assert repo.get_story_for_experience("u1", 8) is None
    assert repo.get_story_for_space("u1", 7, loaded.problem_space_id) == loaded
    assert repo.get_story_for_space("u1", 7, "ps-elsewhere") is None


def test_one_story_per_experience_and_problem_space(repo: ProjectStoryRepository) -> None:
    first = repo.upsert_draft("u1", 7, _CONTENT)
    replaced = repo.upsert_draft("u1", 7, replace(_CONTENT, synthesis_hash="def456"))
    other = repo.upsert_draft("u1", 8, _CONTENT)

    assert replaced.id == first.id  # same space → same row
    assert replaced.content.synthesis_hash == "def456"
    assert other.id != first.id
    assert len(repo.list_stories("u1")) == 2


def test_stories_of_different_spaces_coexist_on_one_entity(
    repo: ProjectStoryRepository,
) -> None:
    """v3.1: one entity holds one story per problem space, keyed independently."""
    space_story = repo.upsert_draft("u1", 7, _CONTENT)
    leftover = repo.upsert_draft("u1", 7, StoryContent(synthesis_hash="leftover"))

    assert leftover.id != space_story.id
    assert leftover.problem_space_id == LEFTOVER_PROBLEM_SPACE_ID
    assert leftover.content.bundle_status == "missing_result"
    stories = repo.list_stories_for_experience("u1", 7)
    assert [s.id for s in stories] == [space_story.id, leftover.id]
    # The single-story convenience accessor returns the first by id.
    first = repo.get_story_for_experience("u1", 7)
    assert first is not None and first.id == space_story.id


def test_replacing_a_pending_draft_resets_it_to_draft(repo: ProjectStoryRepository) -> None:
    story = repo.upsert_draft("u1", 7, _CONTENT)
    repo.transition_story(story.id, StoryReviewStatus.PENDING_REVIEW)

    replaced = repo.upsert_draft("u1", 7, replace(_CONTENT, synthesis_hash="fresh"))
    assert replaced.id == story.id
    assert replaced.review_status is StoryReviewStatus.DRAFT  # a new draft needs a new review


def test_approval_is_a_timestamped_human_act(repo: ProjectStoryRepository) -> None:
    story = repo.upsert_draft("u1", 7, _CONTENT)
    repo.transition_story(story.id, StoryReviewStatus.PENDING_REVIEW)
    approved = repo.transition_story(story.id, StoryReviewStatus.APPROVED)
    assert approved.review_status is StoryReviewStatus.APPROVED
    assert approved.reviewed_at is not None


def test_exclusion_requires_a_retained_reason(repo: ProjectStoryRepository) -> None:
    story = repo.upsert_draft("u1", 7, _CONTENT)
    repo.transition_story(story.id, StoryReviewStatus.PENDING_REVIEW)
    with pytest.raises(ExclusionReasonRequiredError):
        repo.transition_story(story.id, StoryReviewStatus.EXCLUDED)
    excluded = repo.transition_story(
        story.id, StoryReviewStatus.EXCLUDED, decision_note="portfolio inventory"
    )
    assert excluded.decision_note == "portfolio inventory"
    assert excluded.reviewed_at is not None


@pytest.mark.parametrize("decision", [StoryReviewStatus.APPROVED, StoryReviewStatus.EXCLUDED])
def test_resynthesis_never_replaces_a_human_decision(
    repo: ProjectStoryRepository, decision: StoryReviewStatus
) -> None:
    story = repo.upsert_draft("u1", 7, _CONTENT)
    repo.transition_story(story.id, StoryReviewStatus.PENDING_REVIEW)
    repo.transition_story(story.id, decision, decision_note="a retained reason")

    with pytest.raises(StoryReplaceError):
        repo.upsert_draft("u1", 7, replace(_CONTENT, synthesis_hash="fresh"))
    unchanged = repo.get_story(story.id)
    assert unchanged is not None and unchanged.review_status is decision
    assert unchanged.content == stamped_content(_CONTENT)


def test_invalidation_is_the_explicit_way_back_to_draft(repo: ProjectStoryRepository) -> None:
    """The roster cascade (merge/discard) and the explicit un-approve: back to draft,
    prior approval retained in the decision log, then re-synthesis may proceed. It
    sweeps every one of the entity's stories (one per problem space)."""
    story = repo.upsert_draft("u1", 7, _CONTENT)
    repo.transition_story(story.id, StoryReviewStatus.PENDING_REVIEW)
    repo.transition_story(story.id, StoryReviewStatus.APPROVED)
    sibling = repo.upsert_draft("u1", 7, StoryContent(synthesis_hash="leftover"))

    invalidated = repo.invalidate_story(7, reason="entity merged into 'MassDEP'")
    assert [s.id for s in invalidated] == [story.id, sibling.id]
    primary = invalidated[0]
    assert primary.review_status is StoryReviewStatus.DRAFT
    assert primary.reviewed_at is None
    assert primary.decision_note is not None
    assert "previously approved" in primary.decision_note
    assert "entity merged" in primary.decision_note
    assert invalidated[1].review_status is StoryReviewStatus.DRAFT

    refreshed = repo.upsert_draft("u1", 7, replace(_CONTENT, synthesis_hash="post-merge"))
    assert refreshed.id == story.id
    assert refreshed.content.synthesis_hash == "post-merge"


def test_invalidating_a_missing_story_returns_nothing(repo: ProjectStoryRepository) -> None:
    assert repo.invalidate_story(404, reason="entity discarded") == []


def test_record_selection_stamps_ready_and_respects_decisions(
    repo: ProjectStoryRepository,
) -> None:
    """Selection persists the picks and stamps ready; a decided story refuses; a
    machine re-draft resets the selection with the content."""
    story = repo.upsert_draft("u1", 7, _CONTENT)
    action_id = _CONTENT.actions[0].component_id
    result_id = _CONTENT.results[0].component_id

    selected = repo.record_selection(story.id, action_id, result_id)
    assert selected.content.selected_action_id == action_id
    assert selected.content.selected_result_id == result_id
    assert selected.content.bundle_status == "ready"
    assert selected.review_status is StoryReviewStatus.DRAFT  # not a review decision

    # A new machine draft supersedes the selection (new candidates, new picks).
    refreshed = repo.upsert_draft("u1", 7, replace(_CONTENT, synthesis_hash="fresh"))
    assert refreshed.content.selected_action_id is None
    assert refreshed.content.bundle_status == "requires_user_selection"

    repo.transition_story(story.id, StoryReviewStatus.PENDING_REVIEW)
    repo.transition_story(story.id, StoryReviewStatus.APPROVED)
    with pytest.raises(StoryReplaceError):
        repo.record_selection(story.id, action_id, result_id)
    with pytest.raises(LookupError):
        repo.record_selection(404, action_id, result_id)


def test_delete_draft_removes_machine_drafts_only(repo: ProjectStoryRepository) -> None:
    """Stale-space cleanup deletes drafts; a human decision is never deleted."""
    draft = repo.upsert_draft("u1", 7, _CONTENT)
    repo.delete_draft(draft.id)
    assert repo.get_story(draft.id) is None
    repo.delete_draft(draft.id)  # deleting a missing story is a no-op

    decided = repo.upsert_draft("u1", 8, _CONTENT)
    repo.transition_story(decided.id, StoryReviewStatus.PENDING_REVIEW)
    repo.transition_story(decided.id, StoryReviewStatus.APPROVED)
    with pytest.raises(StoryReplaceError):
        repo.delete_draft(decided.id)
    assert repo.get_story(decided.id) is not None


def test_terminal_statuses_reject_ordinary_transitions(repo: ProjectStoryRepository) -> None:
    story = repo.upsert_draft("u1", 7, _CONTENT)
    with pytest.raises(InvalidTransitionError):  # draft can only move to pending_review
        repo.transition_story(story.id, StoryReviewStatus.APPROVED)
    repo.transition_story(story.id, StoryReviewStatus.PENDING_REVIEW)
    repo.transition_story(story.id, StoryReviewStatus.APPROVED)
    with pytest.raises(InvalidTransitionError):
        repo.transition_story(story.id, StoryReviewStatus.PENDING_REVIEW)


# --- Normalizer versioning (§2.2): evidence rows carry their normalizer generation -------


@pytest.mark.parametrize("kind", ["in_memory", "sql"])
def test_evidence_rows_are_stamped_with_the_normalizer_version(kind: str, tmp_path: Path) -> None:
    claims_repo: ClaimRepository
    if kind == "in_memory":
        claims_repo = InMemoryClaimRepository()
    else:
        engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'evidence.db'}")
        Base.metadata.create_all(engine)
        claims_repo = SqlClaimRepository(create_session_factory(engine))

    stored = claims_repo.upsert_evidence(
        "u1", EvidenceChunk(SOURCE_GITHUB_COMMIT, "repo@abc", "normalized text")
    )
    assert stored.normalization_version == NORMALIZATION_VERSION

    updated = claims_repo.upsert_evidence(
        "u1", EvidenceChunk(SOURCE_GITHUB_COMMIT, "repo@abc", "re-normalized text")
    )
    assert updated.id == stored.id
    assert updated.normalization_version == NORMALIZATION_VERSION
