"""The project-story review layer: the queue, the readiness view, and the actions.

Phase 3's human-in-the-loop core (docs/ARCHITECTURE_V3.md §7), and the review unit
that superseded the retired V2 claim review queue. Four actions on a card:

* **approve** — the story becomes canonical. Refused (HTTP 409) unless the resume-ready
  gate passes: a Problem (evidenced or attested), ≥1 Action, and a Result. This is the
  gate the V2/V3 audit found defined in the domain but *enforced nowhere at runtime*.
* **answer** — the reviewer types a Problem or Result the sources lack; it persists as a
  ``story:{id}:{component}`` user attestation (the same evidence table claim edits use)
  and readiness recomputes locally. A Problem answer faces the same structural bar an
  extracted Problem does (§3.6).
* **exclude** — with a required, retained reason (portfolio/low-value/etc.).
* (**synthesize** builds the drafts — ``services/story_synthesis.py``.)

``build_story_view`` is the one place readiness is computed for a card, so the approve
gate and the rendered card can never disagree.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.domain.claims import (
    SOURCE_USER_ATTESTATION,
    Claim,
    ClaimField,
    ClaimRepository,
    EvidenceChunk,
    Experience,
    ResultKind,
)
from app.domain.project_story import (
    COMPONENT_PROBLEM,
    COMPONENT_RESULT,
    ProjectStory,
    ProjectStoryRepository,
    ResultCandidate,
    StoryReadiness,
    StoryReviewStatus,
    assert_approvable,
    assess_problem_text,
    compute_readiness,
    detect_metric_conflicts,
)

_DECIDED = (StoryReviewStatus.APPROVED, StoryReviewStatus.EXCLUDED)


class StoryAnswerError(ValueError):
    """A typed answer named an invalid component or failed the structural bar (HTTP 422)."""


class StoryDecidedError(ValueError):
    """An edit was attempted on a story a human already decided on (HTTP 409)."""


@dataclass(frozen=True)
class StoryView:
    """A story plus everything a card needs — computed once, shared by gate and JSON."""

    story: ProjectStory
    readiness: StoryReadiness
    claims_by_id: dict[int, Claim]
    experience: Experience | None


def _result_candidates(
    claims: list[Claim], claim_repository: ClaimRepository
) -> list[ResultCandidate]:
    """Every outcome quote in the entity's claims, for cross-source conflict detection."""
    candidates: list[ResultCandidate] = []
    for claim in claims:
        if claim.result_kind is ResultKind.MISSING:
            continue
        for link in claim.evidence:
            if link.field is ClaimField.RESULT and (link.outcome_quote or "").strip():
                stored = claim_repository.get_evidence(link.evidence_id)
                candidates.append(
                    ResultCandidate(
                        claim_id=claim.id,
                        quote=link.outcome_quote or "",
                        source_ref=stored.source_ref if stored else "",
                        source_date=stored.created_at if stored else None,
                    )
                )
    return candidates


def _attestation(
    claim_repository: ClaimRepository, story: ProjectStory, component: str
) -> str | None:
    stored = claim_repository.get_evidence_by_ref(
        story.user_id, SOURCE_USER_ATTESTATION, f"story:{story.id}:{component}"
    )
    return stored.chunk_text if stored is not None else None


def build_story_view(story: ProjectStory, claim_repository: ClaimRepository) -> StoryView:
    """Compute the readiness view for one story from its claims, answers, and conflicts."""
    claims = [
        c
        for c in claim_repository.list_claims(story.user_id)
        if c.experience_id == story.experience_id
    ]
    claims_by_id = {c.id: c for c in claims}

    attested_components: set[str] = set()
    attested_problem_text = _attestation(claim_repository, story, COMPONENT_PROBLEM)
    if attested_problem_text is not None:
        attested_components.add(COMPONENT_PROBLEM)
    if _attestation(claim_repository, story, COMPONENT_RESULT) is not None:
        attested_components.add(COMPONENT_RESULT)

    readiness = compute_readiness(
        story.content,
        story.experience_id,
        claims_by_id,
        attested_components=attested_components,
        attested_problem_text=attested_problem_text,
        evidence_count=len(
            claim_repository.list_assigned_evidence(story.user_id, story.experience_id)
        ),
        conflicts=detect_metric_conflicts(_result_candidates(claims, claim_repository)),
    )
    experience = next(
        (
            e
            for e in claim_repository.list_experiences(story.user_id)
            if e.id == story.experience_id
        ),
        None,
    )
    return StoryView(
        story=story, readiness=readiness, claims_by_id=claims_by_id, experience=experience
    )


def story_queue(
    story_repository: ProjectStoryRepository,
    user_id: str,
    *,
    status: StoryReviewStatus | None = None,
) -> list[ProjectStory]:
    """The user's stories (optionally one review state); the queue is ``pending_review``."""
    return story_repository.list_stories(user_id, status)


def approve_story(
    story_repository: ProjectStoryRepository,
    claim_repository: ClaimRepository,
    story_id: int,
    *,
    now: datetime | None = None,
) -> ProjectStory:
    """Approve a story — but only through the resume-ready gate (the runtime 409).

    The gate is enforced here (server-side), independently of any client, exactly as it
    is at render time: a story missing a Problem, Action, or Result cannot be approved.
    """
    story = story_repository.get_story(story_id)
    if story is None:
        raise LookupError(f"no story with id {story_id}")
    assert_approvable(build_story_view(story, claim_repository).readiness)
    return story_repository.transition_story(story_id, StoryReviewStatus.APPROVED, reviewed_at=now)


def exclude_story(
    story_repository: ProjectStoryRepository, story_id: int, reason: str
) -> ProjectStory:
    """Exclude a story with a required, retained reason (portfolio / low-value / etc.)."""
    return story_repository.transition_story(
        story_id, StoryReviewStatus.EXCLUDED, decision_note=reason
    )


def attest_story_component(
    story_repository: ProjectStoryRepository,
    claim_repository: ClaimRepository,
    story_id: int,
    component: str,
    text: str,
) -> ProjectStory:
    """Persist a typed Problem/Result answer as a ``story:{id}:{component}`` attestation.

    A Problem answer must clear the same structural bar an extracted Problem does (§3.6).
    Actions have no story-scoped attestation slot — fix an Action by editing its claim.
    The story's review status is unchanged; readiness recomputes at the next read.
    """
    story = story_repository.get_story(story_id)
    if story is None:
        raise LookupError(f"no story with id {story_id}")
    if story.review_status in _DECIDED:
        raise StoryDecidedError(
            f"story {story_id} is {story.review_status.value}; invalidate it before editing"
        )
    if component not in (COMPONENT_PROBLEM, COMPONENT_RESULT):
        raise StoryAnswerError(
            f"component must be {COMPONENT_PROBLEM!r} or {COMPONENT_RESULT!r}, not {component!r}"
        )
    answer = (text or "").strip()
    if not answer:
        raise StoryAnswerError("answer text cannot be empty")
    if component == COMPONENT_PROBLEM:
        reasons = assess_problem_text(answer)
        if reasons:
            raise StoryAnswerError("problem answer is not specific enough: " + "; ".join(reasons))
    claim_repository.upsert_evidence(
        story.user_id,
        EvidenceChunk(SOURCE_USER_ATTESTATION, f"story:{story.id}:{component}", answer),
    )
    return story
