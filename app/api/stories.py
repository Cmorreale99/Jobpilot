"""Project-story review HTTP routes: synthesize, the card queue, approve / answer /
exclude.

This is the story review card's API — the V3 replacement for clicking through 148 raw
claims. Each card carries its Problem / Actions / Result with the cited evidence quotes
resolved under every component, the derived readiness (badges + targeted questions), and
the three decisions. Approval goes through the resume-ready gate server-side: an
incomplete story is a 409, enforced here regardless of what any client sends.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import (
    get_claim_repository,
    get_grouping_store,
    get_story_repository,
    get_validation_log,
)
from app.domain.applications import InvalidTransitionError
from app.domain.bullet import BulletGenerationError
from app.domain.claims import SOURCE_USER_ATTESTATION, ClaimRepository, evidence_source_url
from app.domain.problem_space import GroupingStore
from app.domain.project_story import (
    COMPONENT_PROBLEM,
    COMPONENT_RESULT,
    ComponentPresence,
    ExclusionReasonRequiredError,
    ProjectStory,
    ProjectStoryRepository,
    StoryNotReadyError,
    StoryReviewStatus,
    approval_blockers,
    component_id,
    problem_support_strength,
    resolve_component_evidence,
)
from app.domain.validation_runs import ValidationRunLog
from app.services.problem_space_detector_factory import create_problem_space_detector
from app.services.story_review import (
    BundleSelectionError,
    StoryAnswerError,
    StoryDecidedError,
    StoryView,
    approve_story,
    attest_story_component,
    build_story_view,
    exclude_story,
    generate_story_bullet,
    select_bundle_component,
    story_queue,
)
from app.services.story_synthesis import run_story_synthesis
from app.services.story_synthesizer_factory import create_story_synthesizer

router = APIRouter(prefix="/stories", tags=["stories"])

RepositoryDep = Annotated[ClaimRepository, Depends(get_claim_repository)]
StoryRepositoryDep = Annotated[ProjectStoryRepository, Depends(get_story_repository)]
ValidationLogDep = Annotated[ValidationRunLog, Depends(get_validation_log)]
GroupingStoreDep = Annotated[GroupingStore, Depends(get_grouping_store)]


class AnswerRequest(BaseModel):
    """A typed answer to a card's question: which component, and the text."""

    component: str
    text: str = Field(min_length=1)


class ExcludeRequest(BaseModel):
    """Exclude body; the reason is required and retained (labeled review data)."""

    reason: str = Field(min_length=1)


class SelectRequest(BaseModel):
    """Bundle selection: exactly one action and one result, by candidate id."""

    selected_action_id: str = Field(min_length=1)
    selected_result_id: str = Field(min_length=1)


def _violations_payload(violations: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [
        {"code": v.code, "message": v.message, "next_action": v.next_action} for v in violations
    ]


def _evidence_payload(
    claim_ids: tuple[int, ...],
    view: StoryView,
    repository: ClaimRepository,
) -> list[dict[str, Any]]:
    return [
        {
            "source_type": stored.source_type,
            "source_ref": stored.source_ref,
            "source_url": evidence_source_url(stored.source_type, stored.source_ref),
            "chunk_text": stored.chunk_text,
        }
        for stored in resolve_component_evidence(
            claim_ids, view.claims_by_id, repository.get_evidence
        )
    ]


def _attestation_evidence(text: str, source_ref: str) -> list[dict[str, Any]]:
    return [
        {
            "source_type": SOURCE_USER_ATTESTATION,
            "source_ref": source_ref,
            "source_url": None,
            "chunk_text": text,
        }
    ]


def _serialize_story(view: StoryView, repository: ClaimRepository) -> dict[str, Any]:
    story = view.story
    readiness = view.readiness
    content = story.content

    problem: dict[str, Any] | None = None
    if readiness.problem is ComponentPresence.USER_ATTESTED:
        text = _attestation_text(repository, story, COMPONENT_PROBLEM)
        problem = {
            "text": text,
            "presence": readiness.problem.value,
            "support": "ok",  # a typed answer is the user's own attestation
            "evidence": _attestation_evidence(text or "", f"story:{story.id}:{COMPONENT_PROBLEM}"),
        }
    elif content.problem_text:
        evidence = _evidence_payload(content.problem_refs, view, repository)
        problem = {
            "text": content.problem_text,
            "presence": readiness.problem.value,
            "support": problem_support_strength(
                content.problem_text, [e["chunk_text"] for e in evidence]
            ),
            "evidence": evidence,
        }

    actions = [
        {
            "component_id": action.component_id,
            "summary": action.summary,
            "tools": list(action.tools),
            "claim_ids": list(action.claim_ids),
            "evidence": _evidence_payload(action.claim_ids, view, repository),
        }
        for action in content.actions
    ]

    results = [
        {
            "component_id": result.component_id,
            "text": result.text,
            "outcome_quote": result.outcome_quote,
            "claim_ids": list(result.claim_ids),
            "evidence": _evidence_payload(result.claim_ids, view, repository),
        }
        for result in content.results
    ]
    # A story-answered Result has no claim-backed component; surface the attestation.
    if readiness.result is ComponentPresence.USER_ATTESTED and not results:
        text = _attestation_text(repository, story, COMPONENT_RESULT)
        if text is not None:
            results = [
                {
                    # The same content-hash id ``bundle_from_story`` gives the attested
                    # candidate, so selecting it on the card round-trips to /select.
                    "component_id": component_id("r", text),
                    "text": text,
                    "outcome_quote": text,
                    "claim_ids": [],
                    "evidence": _attestation_evidence(text, f"story:{story.id}:{COMPONENT_RESULT}"),
                }
            ]

    return {
        "id": story.id,
        "experience_id": story.experience_id,
        "experience_name": view.experience.name if view.experience else None,
        "section": view.experience.section.value if view.experience else None,
        "problem_space": {
            "id": story.problem_space_id,
            "label": content.problem_space_label,
            "scope": content.problem_space_scope,
        },
        "bundle_status": content.bundle_status,
        "selected_action_id": content.selected_action_id,
        "selected_result_id": content.selected_result_id,
        "review_status": story.review_status.value,
        "reviewed_at": story.reviewed_at.isoformat() if story.reviewed_at else None,
        "decision_note": story.decision_note,
        "readiness": {
            "problem": readiness.problem.value,
            "actions": readiness.actions,
            "result": readiness.result.value,
            "resume_ready": readiness.resume_ready,
            "missing": list(readiness.missing),
            "blockers": list(approval_blockers(readiness)),
        },
        "problem": problem,
        "actions": actions,
        "results": results,
        "questions": [
            {
                "kind": q.kind.value,
                "component": q.component,
                "text": q.text,
                "quotes": list(q.quotes),
            }
            for q in readiness.questions
        ],
    }


def _attestation_text(
    repository: ClaimRepository, story: ProjectStory, component: str
) -> str | None:
    stored = repository.get_evidence_by_ref(
        story.user_id, SOURCE_USER_ATTESTATION, f"story:{story.id}:{component}"
    )
    return stored.chunk_text if stored is not None else None


# --- routes ------------------------------------------------------------------------------


@router.get("")
def list_stories(
    user_id: str,
    story_repository: StoryRepositoryDep,
    repository: RepositoryDep,
    status: StoryReviewStatus | None = None,
) -> list[dict[str, Any]]:
    """Story cards for the review queue (``?status=pending_review``) or the full set."""
    stories = story_queue(story_repository, user_id, status=status)
    return [_serialize_story(build_story_view(s, repository), repository) for s in stories]


@router.post("/synthesize")
def synthesize(
    user_id: str,
    story_repository: StoryRepositoryDep,
    repository: RepositoryDep,
    validation_log: ValidationLogDep,
    grouping_store: GroupingStoreDep,
    force: bool = False,
) -> dict[str, Any]:
    """Build one story draft per (confirmed entity, problem space), promoting the
    structurally clean ones to ``pending_review`` and quarantining the rest. The synthesizer
    is heuristic (verbatim selection) unless ``STORY_LLM_SYNTHESIS`` is on, in which case
    the LLM composer runs."""
    report = run_story_synthesis(
        user_id,
        repository,
        story_repository,
        synthesizer=create_story_synthesizer(),
        detector=create_problem_space_detector(grouping_store=grouping_store, user_id=user_id),
        validation_log=validation_log,
        force=force,
    )
    return {
        "synthesized": report.synthesized,
        "quarantined": report.quarantined,
        "skipped": report.skipped,
        "failed": report.failed,
        "stale_deleted": report.stale_deleted,
    }


@router.get("/{story_id}")
def get_story(
    story_id: int, story_repository: StoryRepositoryDep, repository: RepositoryDep
) -> dict[str, Any]:
    """One story card."""
    story = story_repository.get_story(story_id)
    if story is None:
        raise HTTPException(status_code=404, detail=f"no story with id {story_id}")
    return _serialize_story(build_story_view(story, repository), repository)


@router.post("/{story_id}/approve")
def approve(
    story_id: int, story_repository: StoryRepositoryDep, repository: RepositoryDep
) -> dict[str, Any]:
    """Approve — refused with 409 unless the resume-ready gate passes."""
    try:
        story = approve_story(story_repository, repository, story_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (StoryNotReadyError, InvalidTransitionError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _serialize_story(build_story_view(story, repository), repository)


@router.post("/{story_id}/answer")
def answer(
    story_id: int,
    body: AnswerRequest,
    story_repository: StoryRepositoryDep,
    repository: RepositoryDep,
) -> dict[str, Any]:
    """Answer a missing-Problem / missing-Result question (a story-scoped attestation)."""
    try:
        story = attest_story_component(
            story_repository, repository, story_id, body.component, body.text
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StoryDecidedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except StoryAnswerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _serialize_story(build_story_view(story, repository), repository)


@router.post("/{story_id}/select")
def select(
    story_id: int,
    body: SelectRequest,
    story_repository: StoryRepositoryDep,
    repository: RepositoryDep,
) -> dict[str, Any]:
    """Record the user's bundle selection (one action + one result) — validator-gated.

    A pick outside the story's own candidates, a cross-space/cross-project
    contamination, a result-less bundle, or a problem-less story is a 409 whose
    detail lists the machine-readable violations; nothing is persisted on refusal.
    """
    try:
        story = select_bundle_component(
            story_repository,
            repository,
            story_id,
            body.selected_action_id,
            body.selected_result_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StoryDecidedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BundleSelectionError as exc:
        raise HTTPException(
            status_code=409, detail={"violations": _violations_payload(exc.violations)}
        ) from exc
    return _serialize_story(build_story_view(story, repository), repository)


@router.post("/{story_id}/bullet")
def bullet(
    story_id: int, story_repository: StoryRepositoryDep, repository: RepositoryDep
) -> dict[str, Any]:
    """Generate the single bullet the recorded selection defines.

    Returns ``{"bullet": {...}}`` on success, or ``{"bullet": null, "follow_up":
    {...}}`` when the bundle has no result to select — the 7-option targeted
    result-type question (answer it via ``POST /answer`` with component ``result``).
    A missing selection or any validator/number-gate failure is a 409.
    """
    try:
        outcome = generate_story_bullet(story_repository, repository, story_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (BundleSelectionError, BulletGenerationError) as exc:
        raise HTTPException(
            status_code=409, detail={"violations": _violations_payload(exc.violations)}
        ) from exc
    if outcome.follow_up is not None:
        return {"bullet": None, "follow_up": outcome.follow_up}
    generated = outcome.bullet
    assert generated is not None  # BulletOutcome invariant: bullet or follow_up
    return {
        "bullet": {
            "text": generated.text,
            "problem_space_id": generated.problem_space_id,
            "bundle_id": generated.bundle_id,
            "action_candidate_id": generated.action_candidate_id,
            "result_candidate_id": generated.result_candidate_id,
            "claim_ids": list(generated.claim_ids),
        },
        "follow_up": None,
    }


@router.post("/{story_id}/exclude")
def exclude(
    story_id: int,
    body: ExcludeRequest,
    story_repository: StoryRepositoryDep,
    repository: RepositoryDep,
) -> dict[str, Any]:
    """Exclude a story from the Master CV with a required, retained reason."""
    try:
        story = exclude_story(story_repository, story_id, body.reason)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExclusionReasonRequiredError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _serialize_story(build_story_view(story, repository), repository)
