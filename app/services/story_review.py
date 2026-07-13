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

v3.1 Increment 4 adds the **selection flow**: ``select_bundle_component`` runs the
user's action/result picks through the four bundle validators against the story's
own persisted candidates (the card IS the bundle — Increment 3's pooling makes the
story's lists the reviewed truth, wider than raw re-detection for single-space
entities) and persists them (``bundle_status=ready``); ``generate_story_bullet``
hard-gates the same way and returns the one composed bullet — or the 7-option
missing-result follow-up when the bundle has no result to select (asked for, never
invented).

``build_story_view`` is the one place readiness is computed for a card, so the approve
gate and the rendered card can never disagree.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.domain.bullet import GeneratedBullet, generate_bullet
from app.domain.bundle_validation import (
    ASK_TARGETED_FOLLOWUP,
    BundleViolation,
    pairing_relationship,
    validate_bundle_selection,
    validate_evidence_boundary,
    validate_pairing_support,
    validate_problem_space_alignment,
    validate_result_presence,
)
from app.domain.claims import (
    SOURCE_USER_ATTESTATION,
    Claim,
    ClaimField,
    ClaimRepository,
    EvidenceChunk,
    Experience,
    ResultKind,
    ResultType,
)
from app.domain.problem_space import PARBundle, bundle_from_story
from app.domain.project_story import (
    COMPONENT_PROBLEM,
    COMPONENT_RESULT,
    MISSING_RESULT_QUESTION,
    ProjectStory,
    ProjectStoryRepository,
    QuestionKind,
    ResultCandidate,
    StoryReadiness,
    StoryReviewStatus,
    assert_approvable,
    assess_problem_text,
    compute_readiness,
    detect_metric_conflicts,
    resolve_component_evidence,
)

_DECIDED = (StoryReviewStatus.APPROVED, StoryReviewStatus.EXCLUDED)

# The targeted missing-result follow-up's options (§ Increment 4): the 7 result
# categories beyond a bare number — the reason v3.1 widened result recognition. A
# quantified figure is of course also welcome; the question text already says so.
RESULT_TYPE_OPTIONS: tuple[str, ...] = tuple(
    t.value for t in ResultType if t is not ResultType.QUANTITATIVE
)

# Selection-flow violation code outside the four validators: the bundle has no
# problem yet (a leftover story) — a bundle is defined by its problem, so selection
# waits for the missing-problem answer.
MISSING_PROBLEM = "missing_problem"
SELECTION_MISSING = "selection_missing"


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


# --- v3.1 selection flow + single-bullet generation (Increment 4) -------------------------


class BundleSelectionError(ValueError):
    """A selection or generation request failed a bundle gate (HTTP 409)."""

    def __init__(self, violations: list[BundleViolation]) -> None:
        super().__init__("; ".join(str(v) for v in violations))
        self.violations = tuple(violations)


@dataclass(frozen=True)
class BulletOutcome:
    """What ``generate_story_bullet`` produced: the one bullet, or the follow-up."""

    bullet: GeneratedBullet | None = None
    follow_up: dict[str, Any] | None = None


def missing_result_follow_up() -> dict[str, Any]:
    """The targeted missing-result question with the 7 result-type options.

    Answering goes through the existing attestation mechanism (``POST /answer`` with
    component ``result``) — the same ``story:{id}:result`` evidence row review uses,
    so the readiness card, the render path, and the bullet path all see the answer.
    """
    return {
        "kind": QuestionKind.MISSING_RESULT.value,
        "component": COMPONENT_RESULT,
        "text": MISSING_RESULT_QUESTION,
        "options": list(RESULT_TYPE_OPTIONS),
        "next_action": ASK_TARGETED_FOLLOWUP,
    }


def _story_bundle(story: ProjectStory, claim_repository: ClaimRepository) -> PARBundle:
    """The story's selection bundle, with attestations resolved (render-path parity)."""
    claims_by_id = {c.id: c for c in claim_repository.list_claims(story.user_id)}
    return bundle_from_story(
        story,
        claims_by_id,
        attested_problem=_attestation(claim_repository, story, COMPONENT_PROBLEM),
        attested_result=_attestation(claim_repository, story, COMPONENT_RESULT),
    )


def _require_problem(bundle: PARBundle) -> None:
    if not bundle.problem.text.strip():
        raise BundleSelectionError(
            [
                BundleViolation(
                    MISSING_PROBLEM,
                    "the story has no Problem (evidenced or attested) — a bundle is "
                    "defined by its problem space; answer the missing-problem "
                    "question first",
                )
            ]
        )


def select_bundle_component(
    story_repository: ProjectStoryRepository,
    claim_repository: ClaimRepository,
    story_id: int,
    selected_action_id: str,
    selected_result_id: str,
) -> ProjectStory:
    """Persist the user's action/result picks — but only through the bundle gates.

    Runs membership (both ids candidates of THIS story's bundle), problem-space
    alignment, and the evidence boundary on every selected component; a bundle with
    no result candidates routes to the follow-up instead of accepting a selection.
    On pass the repository records the selection and stamps ``bundle_status=ready``.
    """
    story = story_repository.get_story(story_id)
    if story is None:
        raise LookupError(f"no story with id {story_id}")
    if story.review_status in _DECIDED:
        raise StoryDecidedError(
            f"story {story_id} is {story.review_status.value}; invalidate it before editing"
        )

    bundle = _story_bundle(story, claim_repository)
    _require_problem(bundle)
    presence = validate_result_presence(bundle)
    if presence:
        raise BundleSelectionError(presence)
    membership = validate_bundle_selection(bundle, selected_action_id, selected_result_id)
    if membership:
        raise BundleSelectionError(membership)

    action = next(c for c in bundle.action_candidates if c.candidate_id == selected_action_id)
    result = next(c for c in bundle.result_candidates if c.candidate_id == selected_result_id)
    violations = list(validate_problem_space_alignment(bundle.problem, action, result))
    for candidate in (bundle.problem, action, result):
        violations.extend(validate_evidence_boundary(candidate))
    # §9.2/§9.3: the pairing itself must be source-supported (same claim / shared
    # cited evidence) or user-attested — the shared problem space is never enough.
    violations.extend(
        validate_pairing_support(
            action, result, _selected_pairing_relationship(story, action, result, claim_repository)
        )
    )
    if violations:
        raise BundleSelectionError(violations)

    return story_repository.record_selection(story_id, selected_action_id, selected_result_id)


def _selected_pairing_relationship(
    story: ProjectStory,
    action: Any,
    result: Any,
    claim_repository: ClaimRepository,
) -> str:
    """Resolve the action→result relationship from claim provenance (§9.1)."""
    claim_ids = {*action.claim_ids, *result.claim_ids}
    claims_by_id = {
        c.id: c for c in claim_repository.list_claims(story.user_id) if c.id in claim_ids
    }
    evidence_ids_by_claim = {
        claim_id: {link.evidence_id for link in claim.evidence}
        for claim_id, claim in claims_by_id.items()
    }
    return pairing_relationship(
        action.claim_ids,
        result.claim_ids,
        evidence_ids_by_claim,
        result_attested=not result.claim_ids,
    )


def generate_story_bullet(
    story_repository: ProjectStoryRepository,
    claim_repository: ClaimRepository,
    story_id: int,
) -> BulletOutcome:
    """The one bullet the story's recorded selection defines — or the follow-up.

    A bundle with no result candidates returns the 7-option missing-result follow-up
    (not an error — it is the flow's answer). A story without a recorded selection is
    a 409: selection is the human step this path exists for. The composed bullet is
    number-grounded against the selected candidates' cited chunks and attestations —
    generation refuses rather than emit an ungrounded figure.
    """
    story = story_repository.get_story(story_id)
    if story is None:
        raise LookupError(f"no story with id {story_id}")

    bundle = _story_bundle(story, claim_repository)
    _require_problem(bundle)
    if validate_result_presence(bundle):
        return BulletOutcome(follow_up=missing_result_follow_up())

    selected_action_id = story.content.selected_action_id
    selected_result_id = story.content.selected_result_id
    if not selected_action_id or not selected_result_id:
        raise BundleSelectionError(
            [
                BundleViolation(
                    SELECTION_MISSING,
                    "the story has no recorded action/result selection — "
                    "POST /stories/{id}/select first",
                )
            ]
        )

    claims_by_id = {c.id: c for c in claim_repository.list_claims(story.user_id)}
    selected_claim_ids: list[int] = []
    selected_action = None
    selected_result = None
    for action in bundle.action_candidates:
        if action.candidate_id == selected_action_id:
            selected_action = action
            selected_claim_ids.extend(action.claim_ids)
    for result in bundle.result_candidates:
        if result.candidate_id == selected_result_id:
            selected_result = result
            selected_claim_ids.extend(result.claim_ids)
    if selected_action is not None and selected_result is not None:
        # §9.2: a bullet implies causality — re-gate the recorded pairing here too,
        # so a selection persisted before a claim edit can never publish unsupported.
        pairing = validate_pairing_support(
            selected_action,
            selected_result,
            _selected_pairing_relationship(
                story, selected_action, selected_result, claim_repository
            ),
        )
        if pairing:
            raise BundleSelectionError(pairing)
    evidence_texts = [
        stored.chunk_text
        for stored in resolve_component_evidence(
            selected_claim_ids, claims_by_id, claim_repository.get_evidence
        )
    ]
    attested_result = _attestation(claim_repository, story, COMPONENT_RESULT)
    if attested_result:
        evidence_texts.append(attested_result)

    bullet = generate_bullet(
        bundle, selected_action_id, selected_result_id, evidence_texts=evidence_texts
    )
    return BulletOutcome(bullet=bullet)
