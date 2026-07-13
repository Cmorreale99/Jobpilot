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

from app.api.deps import (
    get_claim_repository,
    get_source_capture_store,
    get_story_repository,
    get_validation_log,
)
from app.config import Settings, get_settings
from app.domain.applications import InvalidTransitionError
from app.domain.claims import (
    ASSIGNMENT_HUMAN,
    ClaimRepository,
    Experience,
    ExperienceKind,
    ExperienceStatus,
    evidence_source_url,
)
from app.domain.evidence_inventory import evidence_categories, source_importance
from app.domain.project_story import ProjectStoryRepository
from app.domain.roster import RosterDetectionError, roster_review_hints
from app.domain.source_capture import SourceCaptureStore
from app.domain.validation_runs import ValidationRunLog
from app.integrations.drive_factory import create_drive_client
from app.integrations.github_factory import create_github_client
from app.services.roster import (
    list_reviewed_claims_on_superseded_evidence,
    run_overlap_detection,
    run_roster_assignment,
    run_roster_detection,
)

router = APIRouter(prefix="/roster", tags=["roster"])

RepositoryDep = Annotated[ClaimRepository, Depends(get_claim_repository)]
StoryRepositoryDep = Annotated[ProjectStoryRepository, Depends(get_story_repository)]
CaptureStoreDep = Annotated[SourceCaptureStore, Depends(get_source_capture_store)]
ValidationLogDep = Annotated[ValidationRunLog, Depends(get_validation_log)]


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
    # Derived review hints (MASTER CV REPAIR §7.2/§12.4): container/repo-name/generic
    # flags + competing interpretations, computed at read time, never persisted.
    hints = roster_review_hints(experience, repository.list_experiences(experience.user_id))
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
        **hints,
    }


@router.get("")
def list_roster(user_id: str, repository: RepositoryDep) -> list[dict[str, Any]]:
    """The full roster (proposed + confirmed + merged + discarded), review order."""
    return [_serialize(e, repository) for e in repository.list_experiences(user_id)]


@router.post("/detect")
async def detect(
    user_id: str,
    request: Request,
    repository: RepositoryDep,
    capture_store: CaptureStoreDep,
    validation_log: ValidationLogDep,
) -> dict[str, Any]:
    """Run roster detection over the configured sources; new entities land proposed."""
    settings = _settings(request)
    try:
        report = await run_roster_detection(
            create_drive_client(settings),
            create_github_client(settings),
            user_id,
            repository,
            settings,
            capture_store=capture_store,
            validation_log=validation_log,
        )
    except RosterDetectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "documents": report.documents,
        "proposed": [_serialize(e, repository) for e in report.proposed],
        "sources": report.gather.summary(),
        # §4.16/§13.1: discovery vs processing over the actual configured universe.
        "coverage": report.gather.coverage(),
    }


@router.post("/assign")
async def assign(
    user_id: str,
    request: Request,
    repository: RepositoryDep,
    capture_store: CaptureStoreDep,
    validation_log: ValidationLogDep,
) -> dict[str, Any]:
    """Chunk every source and assign the chunks to the confirmed entities."""
    settings = _settings(request)
    try:
        report = await run_roster_assignment(
            create_drive_client(settings),
            create_github_client(settings),
            user_id,
            repository,
            settings,
            capture_store=capture_store,
            validation_log=validation_log,
        )
    except RosterDetectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "chunks": report.chunks,
        "assigned": report.assigned,
        "unassigned": report.unassigned,
        "pinned": report.pinned,
        "sources": report.gather.summary(),
        # §4.16/§13.1: discovery vs processing over the actual configured universe.
        "coverage": report.gather.coverage(),
        # H5: the per-section ownership decisions and any LLM prompt truncations —
        # both reported, never silent.
        "sections": [
            {
                "source_ref": decision.source_ref,
                "path": decision.path,
                "experience_id": decision.experience_id,
                "method": decision.method,
            }
            for decision in report.sections
        ],
        "truncated_prompts": report.truncated_prompts,
        # §16.7: refs matching multiple confirmed entities — unresolved, awaiting the
        # user's decision (resolve via alias edits or manual evidence assignment).
        "ambiguous": list(report.ambiguous),
        # Documents whose assigner failed (bounded, chunks left unassigned).
        "assignment_failures": list(report.assignment_failures),
        # H6: stale rows visibly superseded, never orphaned — plus every warning.
        "reconciliation": {
            **report.reconciliation.summary(),
            "warnings": list(report.reconciliation.warnings),
        },
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
                "assignment_method": e.assignment_method,
                # H5: group the queue by governing section instead of raw refs.
                "element_id": e.element_id,
                "section_path": e.section_path,
            }
            for e in rows
        ],
    }


@router.get("/evidence")
def evidence_inventory(
    user_id: str,
    repository: RepositoryDep,
    experience_id: int | None = None,
    include_inactive: bool = False,
) -> dict[str, Any]:
    """The full evidence inventory (MASTER CV REPAIR §4.12/§5.4.10/§10.3/§15).

    Every stored evidence row, queryable independently of claim or story readiness:
    source identity + link, exact text, section context, assignment (entity, method,
    active/superseded), DERIVED category (claim-citation facts, never a text guess),
    §6.3 importance tier, and which claims cite it. Non-PAR evidence is inspectable
    here — nothing disappears for failing to form a complete resume bullet.
    """
    claims = repository.list_claims(user_id)
    rows = [
        row
        for row in repository.list_all_evidence(user_id)
        if (include_inactive or row.is_active)
        and (experience_id is None or row.experience_id == experience_id)
    ]
    items = []
    for row in rows:
        cited_by = [
            {"claim_id": claim.id, "field": link.field.value}
            for claim in claims
            for link in claim.evidence
            if link.evidence_id == row.id
        ]
        items.append(
            {
                "id": row.id,
                "source_type": row.source_type,
                "source_ref": row.source_ref,
                "source_url": evidence_source_url(row.source_type, row.source_ref),
                "chunk_text": row.chunk_text,
                "section_path": row.section_path,
                "experience_id": row.experience_id,
                "assignment_method": row.assignment_method,
                "is_active": row.is_active,
                "superseded_by_id": row.superseded_by_id,
                "categories": list(evidence_categories(row, claims)),
                "importance_tier": source_importance(row.source_type, row.source_ref),
                "cited_by": cited_by,
            }
        )
    return {"count": len(items), "items": items}


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
    # A manual assignment is a retained human decision: labeled `human`, it is
    # pinned — machine re-runs (`run_roster_assignment`) never overwrite it (H1).
    updated = repository.assign_evidence(evidence_id, experience.id, method=ASSIGNMENT_HUMAN)
    return {
        "id": updated.id,
        "experience_id": updated.experience_id,
        "source_ref": updated.source_ref,
        "assignment_method": updated.assignment_method,
    }


@router.get("/superseded-reviewed")
def superseded_reviewed(user_id: str, repository: RepositoryDep) -> dict[str, Any]:
    """Reviewed claims citing superseded evidence — decisions on vanished text (H6).

    A re-ingest superseded the rows these approve/reject decisions were made on.
    Nothing is auto-resolved: the human re-reviews (or re-extraction produces fresh
    claims on the current chunking) and this queue empties as they do.
    """
    rows = list_reviewed_claims_on_superseded_evidence(user_id, repository)
    return {
        "count": len(rows),
        "items": [
            {
                "claim_id": claim.id,
                "status": claim.status.value,
                "experience_id": claim.experience_id,
                "action_text": claim.action_text,
                "superseded_evidence": [
                    {
                        "id": stale.id,
                        "source_type": stale.source_type,
                        "source_ref": stale.source_ref,
                        "superseded_by_id": stale.superseded_by_id,
                        "chunk_text": stale.chunk_text,
                    }
                    for stale in stale_rows
                ],
            }
            for claim, stale_rows in rows
        ],
    }


@router.post("/sections/{element_id}/assign")
def assign_section(
    element_id: int,
    body: EvidenceAssignRequest,
    repository: RepositoryDep,
    capture_store: CaptureStoreDep,
) -> dict[str, Any]:
    """Pin a whole section subtree to a CONFIRMED entity (H5).

    The human corrects ownership at the level it was decided: the given element and
    every descendant have their chunks stamped ``human`` — the H1 pin — so machine
    re-runs never re-guess any of them.
    """
    root = capture_store.get_element(element_id)
    if root is None:
        raise HTTPException(status_code=404, detail=f"no source element with id {element_id}")
    tree = capture_store.list_elements(root.document_version_id)
    subtree_ids = {root.id}
    grew = True
    while grew:  # descendants by parent links (depth is tiny; no recursion needed)
        grew = False
        for element in tree:
            if element.parent_id in subtree_ids and element.id not in subtree_ids:
                subtree_ids.add(element.id)
                grew = True
    rows = repository.list_evidence_for_elements(sorted(subtree_ids))
    if not rows:
        raise HTTPException(
            status_code=409,
            detail=f"no evidence chunks are linked to section element {element_id} — "
            "run roster assignment first",
        )
    experience = next(
        (e for e in repository.list_experiences(rows[0].user_id) if e.id == body.experience_id),
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
    for row in rows:
        repository.assign_evidence(row.id, experience.id, method=ASSIGNMENT_HUMAN)
    return {
        "element_id": element_id,
        "experience_id": experience.id,
        "pinned": len(rows),
        "section_path": rows[0].section_path,
    }


@router.post("/{experience_id}/confirm")
def confirm(experience_id: int, repository: RepositoryDep) -> dict[str, Any]:
    """Confirm a proposed entity (``proposed -> confirmed``)."""
    return _transition(repository, experience_id, ExperienceStatus.CONFIRMED)


@router.post("/{experience_id}/discard")
def discard(
    experience_id: int, repository: RepositoryDep, story_repository: StoryRepositoryDep
) -> dict[str, Any]:
    """Discard an entity (terminal; a re-detection never re-proposes it)."""
    result = _transition(repository, experience_id, ExperienceStatus.DISCARDED)
    # Cascade: a discarded entity's story is invalidated (back to draft, prior decision
    # retained) so an approved story never outlives the entity it described.
    story_repository.invalidate_story(experience_id, reason="roster entity discarded")
    return result


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
def merge(
    body: RosterMergeRequest, repository: RepositoryDep, story_repository: StoryRepositoryDep
) -> dict[str, Any]:
    """Fold one entity into another (claims + evidence move; the source is terminal)."""
    try:
        target = repository.merge_experiences(body.source_id, body.target_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # Cascade: the source is gone and the target's evidence changed — invalidate both
    # stories so neither survives as an approved story over a stale entity boundary.
    story_repository.invalidate_story(body.source_id, reason="roster entity merged away")
    story_repository.invalidate_story(body.target_id, reason="roster entities merged")
    return _serialize(target, repository)
