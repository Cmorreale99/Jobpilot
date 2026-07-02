"""Approval-queue HTTP routes: list pending outreach drafts, approve, or discard.

The queue is the human gate in front of any real send: nothing leaves the pipeline
without an explicit approve here (and sending itself only arrives in M6). Status changes
go through the domain state machines, so an illegal action (approving a discarded draft,
re-approving a sent one) is a 409, never silent corruption.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_application_repository
from app.domain.applications import (
    ApplicationRepository,
    InvalidTransitionError,
    OutreachRecord,
    OutreachStatus,
)

router = APIRouter(prefix="/outreach", tags=["outreach"])

RepositoryDep = Annotated[ApplicationRepository, Depends(get_application_repository)]


def _serialize(record: OutreachRecord, repository: ApplicationRepository) -> dict[str, Any]:
    application = repository.get_application(record.application_id)
    return {
        "id": record.id,
        "application_id": record.application_id,
        "status": record.status.value,
        "subject": record.subject,
        "body": record.body,
        "contact": (
            {
                "name": record.contact.name,
                "title": record.contact.title,
                "email": record.contact.email,
                "source": record.contact.source,
            }
            if record.contact
            else None
        ),
        "job_title": application.job_title if application else None,
        "job_company": application.job_company if application else None,
    }


@router.get("/queue")
def list_queue(user_id: str, repository: RepositoryDep) -> list[dict[str, Any]]:
    """Drafts awaiting a human decision, oldest first."""
    return [_serialize(r, repository) for r in repository.list_pending_outreach(user_id)]


def _transition(
    outreach_id: int, new_status: OutreachStatus, repository: ApplicationRepository
) -> dict[str, Any]:
    try:
        record = repository.transition_outreach(outreach_id, new_status)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _serialize(record, repository)


@router.post("/{outreach_id}/approve")
def approve(outreach_id: int, repository: RepositoryDep) -> dict[str, Any]:
    """Approve a draft (``drafted -> approved``). It still does not send until M6."""
    return _transition(outreach_id, OutreachStatus.APPROVED, repository)


@router.post("/{outreach_id}/discard")
def discard(outreach_id: int, repository: RepositoryDep) -> dict[str, Any]:
    """Discard a draft (``drafted -> discarded``). Re-runs will not resurrect it."""
    return _transition(outreach_id, OutreachStatus.DISCARDED, repository)
