"""Claim review HTTP routes: the queue, approve / edit-approve / reject, the section
picker, and Master CV snapshots.

This is the review card's API. Queue entries carry the cited evidence quotes (chunk
text + source refs) so P/A/R render side-by-side with their provenance. Status changes
go through the domain state machine — an illegal action is a 409, never silent
corruption — and a Master CV version can only ever be built from approved claims.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_claim_repository, get_snapshot_store
from app.domain.applications import InvalidTransitionError
from app.domain.claims import (
    Claim,
    ClaimEditError,
    ClaimEdits,
    ClaimRepository,
    CostDimension,
    Experience,
    ExperienceSection,
    Inefficiency,
    RejectionReasonRequiredError,
    ResultKind,
)
from app.domain.master_cv_snapshot import MasterCvSnapshotStore, StoredSnapshot
from app.services.claim_review import (
    approve_claim,
    edit_and_approve_claim,
    reject_claim,
    review_queue,
)
from app.services.master_cv_snapshot import create_master_cv_snapshot

router = APIRouter(tags=["claims"])

RepositoryDep = Annotated[ClaimRepository, Depends(get_claim_repository)]
SnapshotStoreDep = Annotated[MasterCvSnapshotStore, Depends(get_snapshot_store)]


class RejectRequest(BaseModel):
    """Rejection body; the reason is required (and kept — it is labeled data)."""

    reason: str = Field(min_length=1)


class ClaimEditRequest(BaseModel):
    """Edit-then-approve body; omitted fields stay untouched."""

    problem_text: str | None = None
    problem_cost_dimension: CostDimension | None = None
    problem_inefficiency: Inefficiency | None = None
    action_text: str | None = None
    action_tools: list[str] | None = None
    result_text: str | None = None
    result_kind: ResultKind | None = None
    result_metric_json: dict[str, Any] | None = None

    def to_edits(self) -> ClaimEdits:
        return ClaimEdits(
            problem_text=self.problem_text,
            problem_cost_dimension=self.problem_cost_dimension,
            problem_inefficiency=self.problem_inefficiency,
            action_text=self.action_text,
            action_tools=tuple(self.action_tools) if self.action_tools is not None else None,
            result_text=self.result_text,
            result_kind=self.result_kind,
            result_metric_json=self.result_metric_json,
        )


class LayoutRequest(BaseModel):
    """Section picker body: where an experience renders, and in what order."""

    section: ExperienceSection | None = None
    sort_order: int | None = Field(default=None, ge=0)


def _serialize_claim(claim: Claim, repository: ClaimRepository) -> dict[str, Any]:
    evidence: list[dict[str, Any]] = []
    for link in claim.evidence:
        stored = repository.get_evidence(link.evidence_id)
        evidence.append(
            {
                "evidence_id": link.evidence_id,
                "field": link.field.value,
                "outcome_quote": link.outcome_quote,
                "source_type": stored.source_type if stored else None,
                "source_ref": stored.source_ref if stored else None,
                "chunk_text": stored.chunk_text if stored else None,
            }
        )
    return {
        "id": claim.id,
        "experience_id": claim.experience_id,
        "status": claim.status.value,
        "problem_text": claim.problem_text,
        "problem_cost_dimension": (
            claim.problem_cost_dimension.value if claim.problem_cost_dimension else None
        ),
        "problem_inefficiency": (
            claim.problem_inefficiency.value if claim.problem_inefficiency else None
        ),
        "action_text": claim.action_text,
        "action_tools": list(claim.action_tools),
        "result_text": claim.result_text,
        "result_kind": claim.result_kind.value,
        "result_status": claim.result_status.value,
        "result_metric_json": claim.result_metric_json,
        "validation_flags": list(claim.validation_flags),
        "review_note": claim.review_note,
        "evidence": evidence,
    }


def _serialize_experience(experience: Experience) -> dict[str, Any]:
    return {
        "id": experience.id,
        "name": experience.name,
        "subtitle": experience.subtitle,
        "dates": experience.dates,
        "section": experience.section.value,
        "sort_order": experience.sort_order,
    }


def _serialize_snapshot(snapshot: StoredSnapshot) -> dict[str, Any]:
    return {
        "user_id": snapshot.user_id,
        "version": snapshot.version,
        "content_hash": snapshot.content_hash,
        "content": snapshot.content,
    }


# --- review queue ---------------------------------------------------------------------


@router.get("/claims/queue")
def list_queue(
    user_id: str, repository: RepositoryDep, missing_results: bool = False
) -> list[dict[str, Any]]:
    """Claims awaiting review, with cited evidence for the review cards.

    ``missing_results=true`` filters to the Missing-Results queue — strong
    Problem/Action pairs waiting for an impact statement or approval to omit.
    """
    claims = review_queue(repository, user_id, missing_results_only=missing_results)
    return [_serialize_claim(c, repository) for c in claims]


@router.post("/claims/{claim_id}/approve")
def approve(claim_id: int, repository: RepositoryDep) -> dict[str, Any]:
    """Approve as-is (``pending_review -> approved``)."""
    try:
        claim = approve_claim(repository, claim_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _serialize_claim(claim, repository)


@router.post("/claims/{claim_id}/reject")
def reject(claim_id: int, body: RejectRequest, repository: RepositoryDep) -> dict[str, Any]:
    """Reject with a required reason. The claim is retained, never deleted."""
    try:
        claim = reject_claim(repository, claim_id, body.reason)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RejectionReasonRequiredError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _serialize_claim(claim, repository)


@router.post("/claims/{claim_id}/edit-approve")
def edit_approve(
    claim_id: int, body: ClaimEditRequest, repository: RepositoryDep
) -> dict[str, Any]:
    """Edit fields, stamp ``user_attestation`` provenance on each, then approve."""
    try:
        claim = edit_and_approve_claim(repository, claim_id, body.to_edits())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ClaimEditError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _serialize_claim(claim, repository)


# --- experiences (section picker) -------------------------------------------------------


@router.get("/experiences")
def list_experiences(user_id: str, repository: RepositoryDep) -> list[dict[str, Any]]:
    """The user's experiences in render order, with their section assignment."""
    return [_serialize_experience(e) for e in repository.list_experiences(user_id)]


@router.post("/experiences/{experience_id}/layout")
def update_layout(
    experience_id: int, body: LayoutRequest, repository: RepositoryDep
) -> dict[str, Any]:
    """Reassign which section an experience renders under, and its order."""
    if body.section is None and body.sort_order is None:
        raise HTTPException(status_code=422, detail="provide section and/or sort_order")
    try:
        experience = repository.update_experience_layout(
            experience_id, section=body.section, sort_order=body.sort_order
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _serialize_experience(experience)


# --- master CV snapshots -----------------------------------------------------------------


@router.post("/master-cv/snapshots")
def create_snapshot(
    user_id: str, repository: RepositoryDep, store: SnapshotStoreDep
) -> dict[str, Any]:
    """Snapshot the approved claims as a Master CV version (idempotent)."""
    return _serialize_snapshot(create_master_cv_snapshot(user_id, repository, store))


@router.get("/master-cv/snapshots/latest")
def latest_snapshot(user_id: str, store: SnapshotStoreDep) -> dict[str, Any]:
    """The latest approved-claims Master CV version."""
    snapshot = store.get_latest(user_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"no Master CV snapshot for {user_id}")
    return _serialize_snapshot(snapshot)
