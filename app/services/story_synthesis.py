"""Story synthesis: build Project Story drafts per confirmed entity, offline.

Phase 3's machine half (docs/ARCHITECTURE_V3.md §7), generalized by v3.1 Increment 3:
the synthesis unit is one **problem space** of an entity, not the entity. Each run
detects an entity's spaces (``detect_problem_spaces``, deterministic), synthesizes one
draft per space from that space's member claims only — so a draft can never blend
FedEx-integrity work with Pacifica-automation results — and keeps one **leftover**
story per entity for the claims no space covers (no bar-clearing problem, no
unambiguous co-citation) and for claim-less entities, so nothing v3's single story
surfaced is silently lost.

It mirrors claim extraction's skip economics and loud-failure discipline but
**authors nothing**: the synthesizer picks claim text verbatim (§3.8), the structural
validator gates it, and only a clean draft is persisted and promoted to
``pending_review`` for the human. Fatal structural findings **quarantine** the draft —
it is not persisted (broken content never reaches the review queue) and the finding is
logged to ``validation_runs``.

Re-synthesis never overwrites a human: a decided story (approved/excluded), a story
carrying typed answers, and a story whose inputs are unchanged are all skipped — the
same guarantees the repository's ``upsert_draft`` enforces, checked here first so the
skip is observable and cheap. A machine draft whose space dissolved under re-detection
is deleted (``stale_deleted``); decided or answered stories are never cleaned up.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field, replace

from app.domain.claims import (
    SOURCE_USER_ATTESTATION,
    Claim,
    ClaimRepository,
    ExperienceStatus,
)
from app.domain.problem_space import (
    ProblemSpace,
    detect_problem_spaces,
    space_claim_ids,
    uncovered_claim_ids,
)
from app.domain.project_story import (
    COMPONENT_PROBLEM,
    COMPONENT_RESULT,
    LEFTOVER_PROBLEM_SPACE_ID,
    HeuristicStorySynthesizer,
    ProjectStoryRepository,
    StoryContent,
    StoryReplaceError,
    StoryReviewStatus,
    StorySynthesisError,
    StorySynthesizer,
    derive_bundle_status,
    story_synthesis_fingerprint,
    validate_story_structure,
)
from app.domain.validation_runs import KIND_STORY_SYNTHESIS, ValidationRunLog

logger = logging.getLogger(__name__)

_DECIDED = (StoryReviewStatus.APPROVED, StoryReviewStatus.EXCLUDED)


@dataclass(frozen=True)
class StorySynthesisReport:
    """What one synthesis run did, per synthesis unit (experience ids; an entity with
    several problem spaces appears once per space). ``stale_deleted`` holds the story
    ids of machine drafts removed because re-detection dissolved their space."""

    synthesized: list[int] = field(default_factory=list)  # fresh pending_review drafts
    quarantined: list[int] = field(default_factory=list)  # fatal findings, not persisted
    skipped: list[int] = field(default_factory=list)  # decided / answered / unchanged
    failed: list[int] = field(default_factory=list)  # synthesis errored (e.g. LLM call failed)
    stale_deleted: list[int] = field(default_factory=list)  # machine drafts of dissolved spaces


def _story_has_answers(claim_repository: ClaimRepository, user_id: str, story_id: int) -> bool:
    """Whether the user has typed any story-scoped answer (never overwrite one)."""
    return any(
        claim_repository.get_evidence_by_ref(
            user_id, SOURCE_USER_ATTESTATION, f"story:{story_id}:{component}"
        )
        is not None
        for component in (COMPONENT_PROBLEM, COMPONENT_RESULT)
    )


def _record(
    validation_log: ValidationRunLog | None,
    user_id: str,
    subject_ref: str,
    *,
    passed: bool,
    detail: Sequence[str] = (),
) -> None:
    if validation_log is not None:
        validation_log.record(
            user_id,
            KIND_STORY_SYNTHESIS,
            subject_ref,
            passed=passed,
            detail=detail,
        )


def _space_content(
    draft: StoryContent, space: ProblemSpace | None, fingerprint: str
) -> StoryContent:
    """Stamp a synthesized draft with its space's identity.

    When the synthesizer found no problem in a detected space's claims (the heuristic
    additionally requires a declared cost dimension; the space bar does not), the
    space's own defining problem fills the slot — verbatim, citing the claims that
    state it — so a per-space story always carries the problem that defines it.
    """
    problem_text = draft.problem_text
    problem_refs = draft.problem_refs
    if space is not None and not (problem_text or "").strip():
        primary = space.bundles[0].problem
        problem_text = primary.text
        problem_refs = primary.claim_ids
    stamped = replace(
        draft,
        problem_text=problem_text,
        problem_refs=problem_refs,
        synthesis_hash=fingerprint,
        problem_space_id=(
            space.problem_space_id if space is not None else LEFTOVER_PROBLEM_SPACE_ID
        ),
        problem_space_label=space.label if space is not None else None,
        problem_space_scope=space.scope if space is not None else None,
        selected_action_id=None,
        selected_result_id=None,
        bundle_status=None,
    )
    return replace(stamped, bundle_status=derive_bundle_status(stamped))


def _synthesis_units(
    experience_id: int, claims: list[Claim]
) -> list[tuple[ProblemSpace | None, list[Claim]]]:
    """One unit per detected space, plus the leftover unit when anything is uncovered.

    With exactly ONE detected space, the entity's uncovered claims pool into it — v3's
    single-story semantics, safe because there is no second space to contaminate (and
    most real entities state one problem while their result claims cite different
    chunks, so strict co-citation would exile the results to a problem-less story).
    With several spaces, attachment stays strict: uncovered claims go to the entity's
    leftover story (missing-problem question) rather than being guessed into a space —
    the Cooper.ai dataset-delivery case. A claim-less entity (or one with no detectable
    spaces) still yields exactly one leftover unit — its story carries the classify /
    missing-problem questions, so per-space synthesis never loses an entity v3 surfaced.
    """
    spaces = detect_problem_spaces(experience_id, claims)
    uncovered = set(uncovered_claim_ids(experience_id, claims, spaces))
    if len(spaces) == 1:
        member_ids = space_claim_ids(spaces[0]) | uncovered
        return [(spaces[0], [c for c in claims if c.id in member_ids])]
    units: list[tuple[ProblemSpace | None, list[Claim]]] = []
    for space in spaces:
        member_ids = space_claim_ids(space)
        units.append((space, [c for c in claims if c.id in member_ids]))
    if uncovered or not spaces:
        units.append((None, [c for c in claims if c.id in uncovered]))
    return units


def run_story_synthesis(
    user_id: str,
    claim_repository: ClaimRepository,
    story_repository: ProjectStoryRepository,
    *,
    synthesizer: StorySynthesizer | None = None,
    validation_log: ValidationRunLog | None = None,
    experience_ids: Collection[int] | None = None,
    force: bool = False,
) -> StorySynthesisReport:
    """Synthesize one Project Story draft per (confirmed entity, problem space).

    For each confirmed entity: detect its problem spaces, then per space fingerprint
    the space's member claims + the entity's assigned evidence; skip when a human
    already decided (approved/excluded), when typed answers exist, or when the inputs
    are unchanged (unless ``force``). Otherwise run the ``synthesizer`` over the
    space's claims only (the heuristic verbatim selector by default; the flag-gated
    LLM composer as a drop-in) and the structural validator — a synthesis error skips
    the unit loudly (logged, recorded); fatal structural findings quarantine (logged,
    not persisted); a clean draft is persisted, promoted to ``pending_review``, and
    logged as passing. Machine drafts whose space no longer exists are deleted;
    decided or answered stories are left untouched.

    ``experience_ids`` scopes the run to specific entities (default: all confirmed).
    """
    synthesizer = synthesizer or HeuristicStorySynthesizer()
    report = StorySynthesisReport()
    confirmed = [
        e
        for e in claim_repository.list_experiences(user_id)
        if e.status is ExperienceStatus.CONFIRMED
        and (experience_ids is None or e.id in experience_ids)
    ]
    all_claims = claim_repository.list_claims(user_id)

    for experience in confirmed:
        claims = [c for c in all_claims if c.experience_id == experience.id]
        claims_by_id = {c.id: c for c in claims}
        evidence = claim_repository.list_assigned_evidence(user_id, experience.id)
        units = _synthesis_units(experience.id, claims)
        unit_space_ids = {
            space.problem_space_id if space is not None else LEFTOVER_PROBLEM_SPACE_ID
            for space, _ in units
        }

        for space, unit_claims in units:
            space_id = space.problem_space_id if space is not None else LEFTOVER_PROBLEM_SPACE_ID
            subject_ref = f"experience:{experience.id}:space:{space_id}"
            fingerprint = story_synthesis_fingerprint(unit_claims, evidence, scope=space_id)
            existing = story_repository.get_story_for_space(user_id, experience.id, space_id)

            if existing is not None:
                if existing.review_status in _DECIDED:
                    report.skipped.append(experience.id)  # a human decision, never re-synthesized
                    continue
                if not force and _story_has_answers(claim_repository, user_id, existing.id):
                    report.skipped.append(experience.id)  # typed answers are in-progress work
                    continue
                if not force and existing.content.synthesis_hash == fingerprint:
                    report.skipped.append(experience.id)  # unchanged — extraction_hash economics
                    continue

            try:
                draft = synthesizer.synthesize(
                    experience.id,
                    unit_claims,
                    claim_repository.get_evidence,
                    experience_name=experience.name,
                )
            except StorySynthesisError as exc:
                report.failed.append(experience.id)
                _record(
                    validation_log,
                    user_id,
                    subject_ref,
                    passed=False,
                    detail=[f"synthesis_failed: {exc}"],
                )
                logger.error(
                    "story synthesis failed for experience %s space %s: %s",
                    experience.id,
                    space_id,
                    exc,
                )
                continue

            content = _space_content(draft, space, fingerprint)
            violations = validate_story_structure(
                content, experience.id, claims_by_id, claim_repository.get_evidence
            )
            if violations:
                report.quarantined.append(experience.id)
                _record(
                    validation_log,
                    user_id,
                    subject_ref,
                    passed=False,
                    detail=[str(v) for v in violations],
                )
                logger.warning(
                    "story synthesis quarantined experience %s space %s: %s",
                    experience.id,
                    space_id,
                    "; ".join(str(v) for v in violations),
                )
                continue

            try:
                story = story_repository.upsert_draft(user_id, experience.id, content)
            except StoryReplaceError:
                # A human decided between our read and this write — never overwrite it.
                report.skipped.append(experience.id)
                continue
            story_repository.transition_story(story.id, StoryReviewStatus.PENDING_REVIEW)
            report.synthesized.append(experience.id)
            _record(validation_log, user_id, subject_ref, passed=True)

        # Stale-space cleanup: a machine draft whose space re-detection no longer
        # produces would otherwise sit in the review queue forever. Decided stories
        # and stories carrying typed answers are kept — cleanup never outranks a human.
        for story in story_repository.list_stories_for_experience(user_id, experience.id):
            if story.problem_space_id in unit_space_ids:
                continue
            if story.review_status in _DECIDED:
                continue
            if _story_has_answers(claim_repository, user_id, story.id):
                continue
            story_repository.delete_draft(story.id)
            report.stale_deleted.append(story.id)
            logger.info(
                "deleted stale story %s (experience %s, dissolved space %s)",
                story.id,
                experience.id,
                story.problem_space_id,
            )

    logger.info(
        "story synthesis for %s: %d synthesized, %d quarantined, %d skipped, %d failed, "
        "%d stale drafts deleted",
        user_id,
        len(report.synthesized),
        len(report.quarantined),
        len(report.skipped),
        len(report.failed),
        len(report.stale_deleted),
    )
    return report
