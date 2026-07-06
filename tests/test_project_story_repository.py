"""Project-story repository contract tests, run against both implementations.

The load-bearing semantics (docs/ARCHITECTURE_V3.md §2.1): one story per entity;
machine drafts replace only machine drafts; a human decision (approved/excluded) is
NEVER replaced by re-synthesis; exclusion requires a retained reason; approval is a
timestamped act; and invalidation — the roster-merge/discard cascade and the explicit
un-approve — is the only way back to ``draft``, with the prior decision recorded.
"""

from __future__ import annotations

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
    ExclusionReasonRequiredError,
    ProjectStoryRepository,
    StoryAction,
    StoryContent,
    StoryReplaceError,
    StoryResult,
    StoryReviewStatus,
    component_id,
)
from app.domain.text_normalization import NORMALIZATION_VERSION
from app.services.claim_repository import InMemoryClaimRepository
from app.services.project_story_repository import InMemoryProjectStoryRepository


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
    assert loaded.content == _CONTENT
    assert repo.get_story_for_experience("u1", 7) == loaded
    assert repo.get_story_for_experience("u1", 8) is None


def test_one_story_per_experience(repo: ProjectStoryRepository) -> None:
    first = repo.upsert_draft("u1", 7, _CONTENT)
    replaced = repo.upsert_draft("u1", 7, StoryContent(synthesis_hash="def456"))
    other = repo.upsert_draft("u1", 8, _CONTENT)

    assert replaced.id == first.id
    assert replaced.content.synthesis_hash == "def456"
    assert other.id != first.id
    assert len(repo.list_stories("u1")) == 2


def test_replacing_a_pending_draft_resets_it_to_draft(repo: ProjectStoryRepository) -> None:
    story = repo.upsert_draft("u1", 7, _CONTENT)
    repo.transition_story(story.id, StoryReviewStatus.PENDING_REVIEW)

    replaced = repo.upsert_draft("u1", 7, StoryContent())
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
        repo.upsert_draft("u1", 7, StoryContent())
    unchanged = repo.get_story(story.id)
    assert unchanged is not None and unchanged.review_status is decision
    assert unchanged.content == _CONTENT


def test_invalidation_is_the_explicit_way_back_to_draft(repo: ProjectStoryRepository) -> None:
    """The roster cascade (merge/discard) and the explicit un-approve: back to draft,
    prior approval retained in the decision log, then re-synthesis may proceed."""
    story = repo.upsert_draft("u1", 7, _CONTENT)
    repo.transition_story(story.id, StoryReviewStatus.PENDING_REVIEW)
    repo.transition_story(story.id, StoryReviewStatus.APPROVED)

    invalidated = repo.invalidate_story(7, reason="entity merged into 'MassDEP'")
    assert invalidated is not None
    assert invalidated.review_status is StoryReviewStatus.DRAFT
    assert invalidated.reviewed_at is None
    assert invalidated.decision_note is not None
    assert "previously approved" in invalidated.decision_note
    assert "entity merged" in invalidated.decision_note

    refreshed = repo.upsert_draft("u1", 7, StoryContent(synthesis_hash="post-merge"))
    assert refreshed.id == story.id
    assert refreshed.content.synthesis_hash == "post-merge"


def test_invalidating_a_missing_story_returns_none(repo: ProjectStoryRepository) -> None:
    assert repo.invalidate_story(404, reason="entity discarded") is None


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
