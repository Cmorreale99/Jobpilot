"""V3 Phase 3 — the heuristic story-synthesis service (T2/T8 service halves).

Synthesis creates one pending_review Project Story per confirmed entity, is idempotent
via the synthesis fingerprint, never overwrites a human decision or typed answers, and
quarantines a structurally fatal draft (logged, never persisted).
"""

from __future__ import annotations

from app.domain.claims import (
    SOURCE_GITHUB_COMMIT,
    SOURCE_USER_ATTESTATION,
    ClaimEvidenceRef,
    ClaimField,
    ClaimStatus,
    DraftClaim,
    EvidenceChunk,
    ExperienceSection,
    ExperienceSeed,
    ExperienceStatus,
    ResultKind,
    ResultStatus,
    StorableClaim,
)
from app.domain.project_story import StoryReviewStatus
from app.domain.validation_runs import KIND_STORY_SYNTHESIS
from app.services.claim_repository import InMemoryClaimRepository
from app.services.project_story_repository import InMemoryProjectStoryRepository
from app.services.story_synthesis import run_story_synthesis
from app.services.validation_run_log import InMemoryValidationRunLog

from tests.live_corpus import build_live_corpus


def _confirmed_ids(corpus) -> set[int]:  # type: ignore[no-untyped-def]
    return {
        e.id
        for e in corpus.repository.list_experiences(corpus.user_id)
        if e.status is ExperienceStatus.CONFIRMED
    }


def test_synthesis_creates_one_pending_review_story_per_confirmed_entity() -> None:
    corpus = build_live_corpus()
    stories = InMemoryProjectStoryRepository()
    log = InMemoryValidationRunLog()

    report = run_story_synthesis(corpus.user_id, corpus.repository, stories, validation_log=log)

    confirmed = _confirmed_ids(corpus)
    assert len(confirmed) == 15
    assert set(report.synthesized) == confirmed
    assert report.quarantined == []
    assert report.skipped == []

    pending = stories.list_stories(corpus.user_id, StoryReviewStatus.PENDING_REVIEW)
    assert {s.experience_id for s in pending} == confirmed

    # Claim-less entities still get a (component-less) INCLUDE_OR_INVENTORY card.
    for name in ("Jobpilot", "DS4635", "Paper recommender system"):
        story = stories.get_story_for_experience(corpus.user_id, corpus.entity(name).id)
        assert story is not None
        assert story.content.is_empty()
        assert story.review_status is StoryReviewStatus.PENDING_REVIEW

    passed = log.list_runs(corpus.user_id, KIND_STORY_SYNTHESIS)
    assert len(passed) == 15 and all(run.passed for run in passed)


def test_synthesis_skips_unchanged_inputs_and_reruns_on_force() -> None:
    corpus = build_live_corpus()
    stories = InMemoryProjectStoryRepository()

    first = run_story_synthesis(corpus.user_id, corpus.repository, stories)
    assert len(first.synthesized) == 15

    again = run_story_synthesis(corpus.user_id, corpus.repository, stories)
    assert again.synthesized == []
    assert set(again.skipped) == _confirmed_ids(corpus)

    forced = run_story_synthesis(corpus.user_id, corpus.repository, stories, force=True)
    assert set(forced.synthesized) == _confirmed_ids(corpus)


def test_synthesis_never_overwrites_a_decided_story() -> None:
    corpus = build_live_corpus()
    stories = InMemoryProjectStoryRepository()
    run_story_synthesis(corpus.user_id, corpus.repository, stories)

    massdep = corpus.entity("MassDEP")
    story = stories.get_story_for_experience(corpus.user_id, massdep.id)
    assert story is not None
    stories.transition_story(story.id, StoryReviewStatus.APPROVED)

    # Even a forced re-run never touches a human decision.
    report = run_story_synthesis(corpus.user_id, corpus.repository, stories, force=True)
    assert massdep.id in report.skipped
    assert massdep.id not in report.synthesized
    still = stories.get_story_for_experience(corpus.user_id, massdep.id)
    assert still is not None and still.review_status is StoryReviewStatus.APPROVED


def test_synthesis_preserves_typed_answers_until_forced() -> None:
    corpus = build_live_corpus()
    stories = InMemoryProjectStoryRepository()
    run_story_synthesis(corpus.user_id, corpus.repository, stories)

    massdep = corpus.entity("MassDEP")
    story = stories.get_story_for_experience(corpus.user_id, massdep.id)
    assert story is not None
    original_hash = story.content.synthesis_hash

    # The user answers a question (a story-scoped attestation) AND the inputs change.
    corpus.repository.upsert_evidence(
        corpus.user_id,
        EvidenceChunk(
            SOURCE_USER_ATTESTATION,
            f"story:{story.id}:problem",
            "Analysts reconciled permit exports by hand every week",
        ),
    )
    extra = corpus.repository.upsert_evidence(
        corpus.user_id,
        EvidenceChunk(SOURCE_GITHUB_COMMIT, "massdep@newwork", "chore: more permits"),
    )
    corpus.repository.assign_evidence(extra.id, massdep.id)

    report = run_story_synthesis(corpus.user_id, corpus.repository, stories)
    assert massdep.id in report.skipped
    unchanged = stories.get_story_for_experience(corpus.user_id, massdep.id)
    assert unchanged is not None
    assert unchanged.content.synthesis_hash == original_hash  # answers protected the draft

    forced = run_story_synthesis(corpus.user_id, corpus.repository, stories, force=True)
    assert massdep.id in forced.synthesized
    refreshed = stories.get_story_for_experience(corpus.user_id, massdep.id)
    assert refreshed is not None and refreshed.content.synthesis_hash != original_hash


def test_synthesis_quarantines_a_structurally_fatal_draft() -> None:
    """A claim whose Result states a number absent from its cited evidence produces a
    draft with an ``unsupported_number`` violation — quarantined, logged, not persisted."""
    repo = InMemoryClaimRepository()
    stories = InMemoryProjectStoryRepository()
    log = InMemoryValidationRunLog()
    experience = repo.upsert_experience(
        "u1", ExperienceSeed(name="etl", section=ExperienceSection.PROJECTS_HACKATHONS)
    )
    chunk = EvidenceChunk(SOURCE_GITHUB_COMMIT, "etl@c1", "Reworked the exporter in Python")
    draft = DraftClaim(
        action_text="Reworked the exporter in Python",
        action_tools=("Python",),
        result_text="Cut export errors by 99%",  # 99% appears in NO cited chunk
        result_kind=ResultKind.QUANTIFIED,
        result_metric_json={"resolves": "quality", "metric_text": "99%"},
        evidence=(
            ClaimEvidenceRef(chunk=chunk, field=ClaimField.ACTION),
            ClaimEvidenceRef(
                chunk=chunk, field=ClaimField.RESULT, outcome_quote="Cut export errors by 99%"
            ),
        ),
    )
    repo.replace_unreviewed_claims(
        "u1",
        experience.id,
        [
            StorableClaim(
                draft=draft, status=ClaimStatus.PENDING_REVIEW, result_status=ResultStatus.VERIFIED
            )
        ],
    )

    report = run_story_synthesis("u1", repo, stories, validation_log=log)

    assert report.quarantined == [experience.id]
    assert report.synthesized == []
    assert stories.get_story_for_experience("u1", experience.id) is None  # never persisted
    runs = log.list_runs("u1", KIND_STORY_SYNTHESIS)
    assert len(runs) == 1
    assert runs[0].passed is False
    assert runs[0].subject_ref == f"experience:{experience.id}"
    assert any("unsupported_number" in line for line in runs[0].detail)
