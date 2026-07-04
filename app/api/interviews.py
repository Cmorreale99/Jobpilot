"""Interview HTTP routes: confirm queue, confirm action, prep packets, stage changes.

V2 flow: verified detections land in the **confirm queue** (stage ``detected``);
``POST /interviews/{id}/confirm`` is the human gate that moves one to ``confirmed``
and generates its prep packet — packets exist for confirmed interviews only. Stage
changes go through the domain state machine; an illegal jump is a 409, never silent
corruption.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from starlette.requests import Request

from app.api.deps import (
    get_application_repository,
    get_interview_repository,
    get_snapshot_store,
)
from app.config import Settings, get_settings
from app.domain.applications import ApplicationRepository, InvalidTransitionError
from app.domain.interviews import (
    INTERVIEW_TRANSITIONS,
    Interview,
    InterviewRepository,
    InterviewStage,
)
from app.domain.master_cv_snapshot import MasterCvSnapshotStore
from app.services.interview_scan import confirm_interview
from app.services.prep_factory import create_prep_generator

router = APIRouter(prefix="/interviews", tags=["interviews"])

RepositoryDep = Annotated[InterviewRepository, Depends(get_interview_repository)]
SnapshotDep = Annotated[MasterCvSnapshotStore, Depends(get_snapshot_store)]
ApplicationsDep = Annotated[ApplicationRepository, Depends(get_application_repository)]


def _settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


def _serialize(interview: Interview, *, has_packet: bool) -> dict[str, Any]:
    return {
        "id": interview.id,
        "company": interview.company,
        "job_title": interview.job_title,
        "stage": interview.stage.value,
        "received_at": interview.received_at.isoformat() if interview.received_at else None,
        "gmail_message_id": interview.gmail_message_id,
        "evidence_quote": interview.evidence_quote,
        "has_prep_packet": has_packet,
        "allowed_transitions": sorted(
            stage.value for stage in INTERVIEW_TRANSITIONS.get(interview.stage, frozenset())
        ),
    }


@router.get("")
def list_interviews(user_id: str, repository: RepositoryDep) -> list[dict[str, Any]]:
    """All interviews for the user, oldest first."""
    return [
        _serialize(i, has_packet=repository.get_prep_packet(i.id) is not None)
        for i in repository.list_interviews(user_id)
    ]


@router.get("/queue")
def confirm_queue(user_id: str, repository: RepositoryDep) -> list[dict[str, Any]]:
    """The confirm queue: verified detections awaiting the human confirm decision."""
    return [
        _serialize(i, has_packet=repository.get_prep_packet(i.id) is not None)
        for i in repository.list_interviews(user_id)
        if i.stage is InterviewStage.DETECTED
    ]


@router.get("/{interview_id}")
def get_interview(interview_id: int, repository: RepositoryDep) -> dict[str, Any]:
    """One interview with its prep packet content (if generated)."""
    interview = repository.get_interview(interview_id)
    if interview is None:
        raise HTTPException(status_code=404, detail=f"no interview with id {interview_id}")
    packet = repository.get_prep_packet(interview_id)
    payload = _serialize(interview, has_packet=packet is not None)
    payload["prep_packet"] = packet.content if packet else None
    return payload


@router.post("/{interview_id}/confirm")
def confirm(
    interview_id: int,
    request: Request,
    repository: RepositoryDep,
    snapshot_store: SnapshotDep,
    application_repository: ApplicationsDep,
) -> dict[str, Any]:
    """Confirm a detected interview (``detected -> confirmed``) and generate its packet."""
    existing = repository.get_interview(interview_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"no interview with id {interview_id}")
    settings = _settings(request)
    try:
        interview = confirm_interview(
            repository,
            snapshot_store,
            application_repository,
            create_prep_generator(settings),
            interview_id,
            existing.user_id,
        )
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _serialize(interview, has_packet=repository.get_prep_packet(interview_id) is not None)


@router.post("/{interview_id}/transition")
def transition_interview(
    interview_id: int, payload: dict[str, str], repository: RepositoryDep
) -> dict[str, Any]:
    """Move an interview through its stage machine (scheduled/completed/cancelled)."""
    raw_stage = payload.get("stage", "")
    try:
        new_stage = InterviewStage(raw_stage)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"unknown stage '{raw_stage}'") from exc
    try:
        interview = repository.transition_interview(interview_id, new_stage)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _serialize(interview, has_packet=repository.get_prep_packet(interview_id) is not None)
