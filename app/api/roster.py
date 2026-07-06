"""Roster HTTP routes: detect, review (confirm/rename/merge/discard), assign.

The roster review is the load-bearing human step of V2.1 (docs/V2_AUDIT.md §8):
detection proposes real-world entities from the evidence; the human reshapes the
proposals here; assignment then scopes every evidence chunk to a confirmed entity —
and extraction only ever runs inside those boundaries. Illegal lifecycle jumps are a
409, never silent corruption (same policy as every other state machine).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from starlette.requests import Request

from app.api.deps import get_claim_repository
from app.config import Settings, get_settings
from app.domain.applications import InvalidTransitionError
from app.domain.claims import (
    ClaimRepository,
    Experience,
    ExperienceKind,
    ExperienceStatus,
)
from app.domain.roster import RosterDetectionError
from app.integrations.drive_factory import create_drive_client
from app.integrations.github_factory import create_github_client
from app.services.roster import (
    run_overlap_detection,
    run_roster_assignment,
    run_roster_detection,
)

router = APIRouter(prefix="/roster", tags=["roster"])

RepositoryDep = Annotated[ClaimRepository, Depends(get_claim_repository)]


def _settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


class RosterEditRequest(BaseModel):
    """Human roster edits; omitted fields stay untouched."""

    name: str | None = None
    subtitle: str | None = None
    dates: str | None = None
    kind: ExperienceKind | None = None
    aliases: list[str] | None = None


class RosterMergeRequest(BaseModel):
    source_id: int
    target_id: int


class EvidenceAssignRequest(BaseModel):
    experience_id: int


def _serialize(experience: Experience, repository: ClaimRepository) -> dict[str, Any]:
    assigned = (
        len(repository.list_assigned_evidence(experience.user_id, experience.id))
        if experience.status is ExperienceStatus.CONFIRMED
        else 0
    )
    return {
        "id": experience.id,
        "name": experience.name,
        "kind": experience.kind.value,
        "status": experience.status.value,
        "section": experience.section.value,
        "subtitle": experience.subtitle,
        "dates": experience.dates,
        "aliases": list(experience.aliases),
        "sort_order": experience.sort_order,
        "merged_into_id": experience.merged_into_id,
        "assigned_chunks": assigned,
    }


@router.get("")
def list_roster(user_id: str, repository: RepositoryDep) -> list[dict[str, Any]]:
    """The full roster (proposed + confirmed + merged + discarded), review order."""
    return [_serialize(e, repository) for e in repository.list_experiences(user_id)]


@router.post("/detect")
async def detect(user_id: str, request: Request, repository: RepositoryDep) -> dict[str, Any]:
    """Run roster detection over the configured sources; new entities land proposed."""
    settings = _settings(request)
    try:
        report = await run_roster_detection(
            create_drive_client(settings),
            create_github_client(settings),
            user_id,
            repository,
            settings,
        )
    except RosterDetectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "documents": report.documents,
        "proposed": [_serialize(e, repository) for e in report.proposed],
    }


@router.post("/assign")
async def assign(user_id: str, request: Request, repository: RepositoryDep) -> dict[str, Any]:
    """Chunk every source and assign the chunks to the confirmed entities."""
    settings = _settings(request)
    try:
        report = await run_roster_assignment(
            create_drive_client(settings),
            create_github_client(settings),
            user_id,
            repository,
            settings,
        )
    except RosterDetectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "chunks": report.chunks,
        "assigned": report.assigned,
        "unassigned": report.unassigned,
    }


@router.get("/overlaps")
def overlaps(user_id: str, repository: RepositoryDep) -> list[dict[str, Any]]:
    """Cross-entity duplicate signals (V3 §3.7) — each one a merge prompt.

    Deterministic shared-evidence detection over the confirmed roster: the same
    outcome quote backing Results under two entities, or the same chunk text
    assigned to both. Resolved by POST /roster/merge — never automatically.
    """
    report = run_overlap_detection(user_id, repository)
    return [
        {
            "entity_a": {"id": p.experience_a.id, "name": p.experience_a.name},
            "entity_b": {"id": p.experience_b.id, "name": p.experience_b.name},
            "shared_outcome_quotes": list(p.shared_outcome_quotes),
            "shared_chunk_texts": list(p.shared_chunk_texts),
        }
        for p in report.prompts
    ]


@router.get("/unassigned")
def unassigned(user_id: str, repository: RepositoryDep) -> dict[str, Any]:
    """Evidence chunks no confirmed entity claimed — a review queue, not a log line.

    These never feed extraction (red-team case 3: an unproposed project's chunks
    used to vanish silently). Resolve by assigning to a confirmed entity
    (POST /roster/evidence/{id}/assign) or by confirming a new entity and re-running
    assignment.
    """
    rows = repository.list_unassigned_evidence(user_id)
    return {
        "count": len(rows),
        "items": [
            {
                "id": e.id,
                "source_type": e.source_type,
                "source_ref": e.source_ref,
                "chunk_text": e.chunk_text,
            }
            for e in rows
        ],
    }


@router.post("/evidence/{evidence_id}/assign")
def assign_evidence(
    evidence_id: int, body: EvidenceAssignRequest, repository: RepositoryDep
) -> dict[str, Any]:
    """Manually assign a chunk to a CONFIRMED entity — the human overrides the assigner."""
    stored = repository.get_evidence(evidence_id)
    if stored is None:
        raise HTTPException(status_code=404, detail=f"no evidence with id {evidence_id}")
    experience = next(
        (e for e in repository.list_experiences(stored.user_id) if e.id == body.experience_id),
        None,
    )
    if experience is None:
        raise HTTPException(
            status_code=404, detail=f"no experience with id {body.experience_id} for this user"
        )
    if experience.status is not ExperienceStatus.CONFIRMED:
        raise HTTPException(
            status_code=409,
            detail=f"experience {experience.id} is {experience.status.value}, not confirmed — "
            "evidence assigns to confirmed entities only",
        )
    updated = repository.assign_evidence(evidence_id, experience.id)
    return {
        "id": updated.id,
        "experience_id": updated.experience_id,
        "source_ref": updated.source_ref,
    }


@router.post("/{experience_id}/confirm")
def confirm(experience_id: int, repository: RepositoryDep) -> dict[str, Any]:
    """Confirm a proposed entity (``proposed -> confirmed``)."""
    return _transition(repository, experience_id, ExperienceStatus.CONFIRMED)


@router.post("/{experience_id}/discard")
def discard(experience_id: int, repository: RepositoryDep) -> dict[str, Any]:
    """Discard an entity (terminal; a re-detection never re-proposes it)."""
    return _transition(repository, experience_id, ExperienceStatus.DISCARDED)


def _transition(
    repository: ClaimRepository, experience_id: int, status: ExperienceStatus
) -> dict[str, Any]:
    try:
        experience = repository.set_experience_status(experience_id, status)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _serialize(experience, repository)


@router.patch("/{experience_id}")
def edit(experience_id: int, body: RosterEditRequest, repository: RepositoryDep) -> dict[str, Any]:
    """Rename / retitle / redate / rekind / realias an entity (rename keeps an alias)."""
    try:
        experience = repository.update_experience_details(
            experience_id,
            name=body.name,
            subtitle=body.subtitle,
            dates=body.dates,
            kind=body.kind,
            aliases=tuple(body.aliases) if body.aliases is not None else None,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _serialize(experience, repository)


@router.post("/merge")
def merge(body: RosterMergeRequest, repository: RepositoryDep) -> dict[str, Any]:
    """Fold one entity into another (claims + evidence move; the source is terminal)."""
    try:
        target = repository.merge_experiences(body.source_id, body.target_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _serialize(target, repository)
