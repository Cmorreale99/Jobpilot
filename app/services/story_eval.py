"""Story-synthesis evaluation: score the story set and record the run scorecard (§5).

The story analog of ``eval_extraction`` — offline, deterministic, zero LLM spend. It reads
every story for a user, derives readiness (the same ``build_story_view`` the review card and
approve gate use, so the scorecard can never disagree with them) and the structural
violations, and reduces them to the §5 metrics via the pure ``summarize_story_eval``. The
run's ``boundary_clean`` verdict (no invented metrics, no orphan components, no duplicate
stories, no cross-problem-space contamination — v3.1 Increment 6) is recorded to
``validation_runs`` — the evidence that a synthesis run, heuristic or LLM, stayed truthful.
"""

from __future__ import annotations

import logging

from app.domain.claims import Claim, ClaimRepository
from app.domain.evaluation import StoryEvalInput, StoryEvalReport, summarize_story_eval
from app.domain.problem_space import (
    ProblemSpaceDetector,
    claim_space_ids,
    story_cross_space_claim_ids,
)
from app.domain.project_story import ProjectStoryRepository, validate_story_structure
from app.domain.validation_runs import KIND_STORY_EVAL, ValidationRunLog
from app.services.story_review import build_story_view

logger = logging.getLogger(__name__)


def _normalize(text: str | None) -> str:
    return " ".join((text or "").split()).casefold()


def run_story_eval(
    user_id: str,
    story_repository: ProjectStoryRepository,
    claim_repository: ClaimRepository,
    *,
    detector: ProblemSpaceDetector | None = None,
    validation_log: ValidationRunLog | None = None,
) -> StoryEvalReport:
    """Score the user's stories against the §5 metrics and record the scorecard.

    ``detector`` must match the one synthesis ran with (both default heuristic; both
    factory-created when ``PROBLEM_SPACE_LLM_DETECTION`` is on): the contamination
    invariant judges each story against the claim-to-space partition, and judging
    LLM-partitioned stories against the heuristic partition would flag legitimate
    merges. The partition is computed once per entity, not per story.
    """
    inputs: list[StoryEvalInput] = []
    entity_claims_cache: dict[int, list[Claim]] = {}
    space_mapping_cache: dict[int, dict[int, str]] = {}
    for story in story_repository.list_stories(user_id):
        view = build_story_view(story, claim_repository)
        violations = validate_story_structure(
            story.content, story.experience_id, view.claims_by_id, claim_repository.get_evidence
        )
        result_spans = [
            _normalize(result.outcome_quote or result.text) for result in story.content.results
        ]
        entity_claims = entity_claims_cache.setdefault(
            story.experience_id, list(view.claims_by_id.values())
        )
        if story.experience_id not in space_mapping_cache:
            space_mapping_cache[story.experience_id] = claim_space_ids(
                story.experience_id, entity_claims, detector=detector
            )
        inputs.append(
            StoryEvalInput(
                status=story.review_status,
                readiness=view.readiness,
                violations=violations,
                result_spans=result_spans,
                cross_space_claim_ids=story_cross_space_claim_ids(
                    story, entity_claims, mapping=space_mapping_cache[story.experience_id]
                ),
            )
        )

    report = summarize_story_eval(inputs)
    if validation_log is not None:
        validation_log.record(
            user_id,
            KIND_STORY_EVAL,
            f"user:{user_id}",
            passed=report.boundary_clean,
            detail=report.detail_lines(),
        )
    logger.info("story eval for %s: %s", user_id, ", ".join(report.detail_lines()))
    return report
