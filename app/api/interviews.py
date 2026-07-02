"""Interview HTTP routes: list detected interviews, read prep packets, advance stages.

Stage changes go through the domain state machine — an illegal jump (completing a
cancelled interview) is a 409, never silent corruption.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_interview_repository
from app.domain.applications import InvalidTransitionError
from app.domain.interviews import (
    INTERVIEW_TRANSITIONS,
    Interview,
    InterviewRepository,
    InterviewStage,
)

router = APIRouter(prefix="/interviews", tags=["interviews"])

RepositoryDep = Annotated[InterviewRepository, Depends(get_interview_repository)]


def _serialize(interview: Interview, *, has_packet: bool) -> dict[str, Any]:
    return {
        "id": interview.id,
        "company": interview.company,
        "job_title": interview.job_title,
        "stage": interview.stage.value,
        "received_at": interview.received_at.isoformat() if interview.received_at else None,
        "source_message_id": interview.source_message_id,
        "has_prep_packet": has_packet,
        "allowed_transitions": sorted(
            stage.value for stage in INTERVIEW_TRANSITIONS.get(interview.stage, frozenset())
        ),
    }


@router.get("")
def list_interviews(user_id: str, repository: RepositoryDep) -> list[dict[str, Any]]:
    """All detected interviews for the user, oldest first."""
    return [
        _serialize(i, has_packet=repository.get_prep_packet(i.id) is not None)
        for i in repository.list_interviews(user_id)
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
