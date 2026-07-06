"""Story synthesis: build one Project Story draft per confirmed entity, offline.

Phase 3's machine half (docs/ARCHITECTURE_V3.md §7). It mirrors claim extraction's
skip economics and loud-failure discipline but **authors nothing**: ``select_story_content``
picks claim text verbatim (§3.8), the structural validator gates it, and only a clean
draft is persisted and promoted to ``pending_review`` for the human. Fatal structural
findings **quarantine** the draft — it is not persisted (broken content never reaches
the review queue) and the finding is logged to ``validation_runs``.

Re-synthesis never overwrites a human: a decided story (approved/excluded), a story
carrying typed answers, and a story whose inputs are unchanged are all skipped — the
same guarantees the repository's ``upsert_draft`` enforces, checked here first so the
skip is observable and cheap.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field, replace

from app.domain.claims import SOURCE_USER_ATTESTATION, ClaimRepository, ExperienceStatus
from app.domain.project_story import (
    COMPONENT_PROBLEM,
    COMPONENT_RESULT,
    ProjectStoryRepository,
    StoryReplaceError,
    StoryReviewStatus,
    select_story_content,
    story_synthesis_fingerprint,
    validate_story_structure,
)
from app.domain.validation_runs import KIND_STORY_SYNTHESIS, ValidationRunLog

logger = logging.getLogger(__name__)

_DECIDED = (StoryReviewStatus.APPROVED, StoryReviewStatus.EXCLUDED)


@dataclass(frozen=True)
class StorySynthesisReport:
    """What one synthesis run did, per confirmed entity (experience ids)."""

    synthesized: list[int] = field(default_factory=list)  # fresh pending_review drafts
    quarantined: list[int] = field(default_factory=list)  # fatal findings, not persisted
    skipped: list[int] = field(default_factory=list)  # decided / answered / unchanged


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
    experience_id: int,
    *,
    passed: bool,
    detail: Sequence[str] = (),
) -> None:
    if validation_log is not None:
        validation_log.record(
            user_id,
            KIND_STORY_SYNTHESIS,
            f"experience:{experience_id}",
            passed=passed,
            detail=detail,
        )


def run_story_synthesis(
    user_id: str,
    claim_repository: ClaimRepository,
    story_repository: ProjectStoryRepository,
    *,
    validation_log: ValidationRunLog | None = None,
    experience_ids: Collection[int] | None = None,
    force: bool = False,
) -> StorySynthesisReport:
    """Synthesize one Project Story draft per confirmed entity (select → gate → persist).

    For each confirmed entity: fingerprint its claims + assigned evidence; skip when a
    human already decided (approved/excluded), when typed answers exist, or when the
    inputs are unchanged (unless ``force``). Otherwise select content verbatim and run
    the structural validator — fatal findings quarantine (logged, not persisted); a clean
    draft is persisted and promoted to ``pending_review`` and logged as passing.

    ``experience_ids`` scopes the run to specific entities (default: all confirmed).
    """
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
        evidence = claim_repository.list_assigned_evidence(user_id, experience.id)
        fingerprint = story_synthesis_fingerprint(claims, evidence)
        existing = story_repository.get_story_for_experience(user_id, experience.id)

        if existing is not None:
            if existing.review_status in _DECIDED:
                report.skipped.append(experience.id)  # a human decision is never re-synthesized
                continue
            if not force and _story_has_answers(claim_repository, user_id, existing.id):
                report.skipped.append(experience.id)  # typed answers are in-progress human work
                continue
            if not force and existing.content.synthesis_hash == fingerprint:
                report.skipped.append(experience.id)  # unchanged inputs — extraction_hash economics
                continue

        content = replace(select_story_content(experience.id, claims), synthesis_hash=fingerprint)
        claims_by_id = {c.id: c for c in claims}
        violations = validate_story_structure(
            content, experience.id, claims_by_id, claim_repository.get_evidence
        )
        if violations:
            report.quarantined.append(experience.id)
            _record(
                validation_log,
                user_id,
                experience.id,
                passed=False,
                detail=[str(v) for v in violations],
            )
            logger.warning(
                "story synthesis quarantined experience %s: %s",
                experience.id,
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
        _record(validation_log, user_id, experience.id, passed=True)

    logger.info(
        "story synthesis for %s: %d synthesized, %d quarantined, %d skipped",
        user_id,
        len(report.synthesized),
        len(report.quarantined),
        len(report.skipped),
    )
    return report
