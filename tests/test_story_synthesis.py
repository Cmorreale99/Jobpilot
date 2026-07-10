"""V3 Phase 3 — the heuristic story-synthesis service (T2/T8 service halves).

Synthesis creates one pending_review Project Story per (confirmed entity, problem
space) — v3.1 Increment 3 — is idempotent via the per-space synthesis fingerprint,
never overwrites a human decision or typed answers, quarantines a structurally fatal
draft (logged, never persisted), and deletes machine drafts whose space dissolved.
Single-space entities keep v3's one-story shape (uncovered claims pool into the sole
space); multi-space entities split, with honestly-uncovered claims in a leftover story.
"""

from __future__ import annotations

from app.domain.claims import (
    SOURCE_GITHUB_COMMIT,
    SOURCE_USER_ATTESTATION,
    Claim,
    ClaimEvidenceRef,
    ClaimField,
    ClaimStatus,
    DraftClaim,
    EvidenceChunk,
    Experience,
    ExperienceSection,
    ExperienceSeed,
    ExperienceStatus,
    HeuristicTwoPassExtractor,
    ResultKind,
    ResultStatus,
    StorableClaim,
)
from app.domain.project_story import LEFTOVER_PROBLEM_SPACE_ID, StoryReviewStatus
from app.domain.validation_runs import KIND_STORY_SYNTHESIS
from app.services.claim_repository import InMemoryClaimRepository
from app.services.project_story_repository import InMemoryProjectStoryRepository
from app.services.story_synthesis import run_story_synthesis
from app.services.validation_run_log import InMemoryValidationRunLog

from tests.fixtures.problem_spaces.cooper_ai import cooper_group
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
    assert runs[0].subject_ref == f"experience:{experience.id}:space:ps-leftover"
    assert any("unsupported_number" in line for line in runs[0].detail)


# --- v3.1 Increment 3: one story per (entity, problem space) ------------------------------


def _seed_cooper(
    repo: InMemoryClaimRepository, user_id: str = "u1"
) -> tuple[Experience, list[Claim]]:
    """Ingest the real-shaped Cooper.ai group (FedEx / Pacifica / dataset delivery)
    through the ordinary repository writes, exactly like production extraction."""
    group = cooper_group()
    experience = repo.upsert_experience(user_id, group.experience)
    drafts = HeuristicTwoPassExtractor().extract(group)
    for draft in drafts:
        for ref in draft.evidence:
            stored = repo.upsert_evidence(user_id, ref.chunk)
            repo.assign_evidence(stored.id, experience.id)
    claims = repo.replace_unreviewed_claims(
        user_id,
        experience.id,
        [
            StorableClaim(
                draft=draft,
                status=ClaimStatus.PENDING_REVIEW,
                result_status=ResultStatus.UNVERIFIED,
            )
            for draft in drafts
        ],
    )
    return experience, claims


def _component_texts(story) -> list[str]:  # type: ignore[no-untyped-def]
    return [a.summary for a in story.content.actions] + [r.text for r in story.content.results]


def test_multi_space_entity_gets_one_story_per_space_plus_a_leftover() -> None:
    """Cooper.ai splits: FedEx integrity and Pacifica automation each get their own
    story; the dataset-delivery claims (whose problem clears no pain-point lexicon)
    stay honestly uncovered in the entity's leftover story instead of being blended."""
    repo = InMemoryClaimRepository()
    stories = InMemoryProjectStoryRepository()
    experience, _ = _seed_cooper(repo)

    report = run_story_synthesis("u1", repo, stories)

    entity_stories = stories.list_stories_for_experience("u1", experience.id)
    assert len(entity_stories) == 3
    assert report.synthesized.count(experience.id) == 3
    space_ids = {s.problem_space_id for s in entity_stories}
    assert len(space_ids) == 3 and LEFTOVER_PROBLEM_SPACE_ID in space_ids

    by_space = {s.problem_space_id: s for s in entity_stories}
    leftover = by_space[LEFTOVER_PROBLEM_SPACE_ID]
    spaced = [s for s in entity_stories if s.problem_space_id != LEFTOVER_PROBLEM_SPACE_ID]

    # Each space story carries its own problem verbatim, stamped as the space label.
    def _problem(story) -> str:  # type: ignore[no-untyped-def]
        return story.content.problem_text or ""

    fedex = next(s for s in spaced if "FedEx" in _problem(s))
    pacifica = next(s for s in spaced if "manual file preparation" in _problem(s))
    assert fedex.content.problem_space_label == fedex.content.problem_text
    assert fedex.content.bundle_status == "requires_user_selection"
    assert fedex.content.selected_action_id is None

    # The alignment boundary: no story mixes FedEx content with Pacifica content.
    fedex_texts = " | ".join(_component_texts(fedex))
    pacifica_texts = " | ".join(_component_texts(pacifica))
    assert "195K+" in fedex_texts and "warehouse refreshes" not in fedex_texts
    assert "warehouse refreshes" in pacifica_texts and "FedEx" not in pacifica_texts

    # The leftover story keeps the dataset-delivery work reviewable: no problem
    # (the §3.6 missing-problem question fires), the delivery Results intact.
    assert leftover.content.problem_text is None
    assert leftover.content.problem_space_label is None
    leftover_texts = " | ".join(_component_texts(leftover))
    assert "Delivered five production Snowflake datasets" in leftover_texts


def test_single_space_entities_keep_the_v3_one_story_shape() -> None:
    """The live corpus's single-space entities pool their problem-less claims into the
    sole space (v3 semantics — results cite different chunks than problems, and strict
    co-citation would exile them to a problem-less story)."""
    corpus = build_live_corpus()
    stories = InMemoryProjectStoryRepository()
    run_story_synthesis(corpus.user_id, corpus.repository, stories)

    massdep = corpus.entity("MassDEP")
    entity_stories = stories.list_stories_for_experience(corpus.user_id, massdep.id)
    assert len(entity_stories) == 1
    story = entity_stories[0]
    assert story.problem_space_id != LEFTOVER_PROBLEM_SPACE_ID
    assert story.content.problem_text  # the space's defining problem
    assert story.content.results  # pooled results from problem-less claims


def test_stale_space_drafts_are_deleted_but_decisions_survive() -> None:
    """When re-detection dissolves spaces (their problem claims were rejected), the
    machine drafts for those spaces are deleted; a decided story is never touched."""
    repo = InMemoryClaimRepository()
    stories = InMemoryProjectStoryRepository()
    experience, claims = _seed_cooper(repo)
    run_story_synthesis("u1", repo, stories)

    before = stories.list_stories_for_experience("u1", experience.id)
    assert len(before) == 3
    fedex_story = next(s for s in before if "FedEx" in (s.content.problem_text or ""))
    stories.transition_story(fedex_story.id, StoryReviewStatus.APPROVED)

    # The human rejects the Pacifica problem-bearing claims → that space dissolves.
    for claim in claims:
        if "manual file preparation" in (claim.problem_text or ""):
            repo.transition_claim(claim.id, ClaimStatus.REJECTED, review_note="not mine")

    report = run_story_synthesis("u1", repo, stories)

    after = stories.list_stories_for_experience("u1", experience.id)
    space_ids = {s.problem_space_id for s in after}
    pacifica_story = next(
        s for s in before if "manual file preparation" in (s.content.problem_text or "")
    )
    assert pacifica_story.id in report.stale_deleted
    assert pacifica_story.problem_space_id not in space_ids
    # The approved FedEx story survived untouched.
    survived = stories.get_story(fedex_story.id)
    assert survived is not None and survived.review_status is StoryReviewStatus.APPROVED
