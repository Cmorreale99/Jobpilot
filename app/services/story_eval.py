"""Story-synthesis evaluation: score the story set and record the run scorecard (§5).

The story analog of ``eval_extraction`` — offline, deterministic, zero LLM spend. It reads
every story for a user, derives readiness (the same ``build_story_view`` the review card and
approve gate use, so the scorecard can never disagree with them) and the structural
violations, and reduces them to the §5 metrics via the pure ``summarize_story_eval``. The
run's ``boundary_clean`` verdict (no invented metrics, no orphan components, no duplicate
stories) is recorded to ``validation_runs`` — the evidence that a synthesis run, heuristic or
LLM, stayed truthful.
"""

from __future__ import annotations

import logging

from app.domain.claims import ClaimRepository
from app.domain.evaluation import StoryEvalInput, StoryEvalReport, summarize_story_eval
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
    validation_log: ValidationRunLog | None = None,
) -> StoryEvalReport:
    """Score the user's stories against the §5 metrics and record the scorecard."""
    inputs: list[StoryEvalInput] = []
    for story in story_repository.list_stories(user_id):
        view = build_story_view(story, claim_repository)
        violations = validate_story_structure(
            story.content, story.experience_id, view.claims_by_id, claim_repository.get_evidence
        )
        result_spans = [
            _normalize(result.outcome_quote or result.text) for result in story.content.results
        ]
        inputs.append(
            StoryEvalInput(
                status=story.review_status,
                readiness=view.readiness,
                violations=violations,
                result_spans=result_spans,
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
