"""Read endpoints the dashboard consumes, plus application status changes.

Everything here is a thin serialization layer over the repository interfaces — no
business logic. The dashboard reads the latest Master CV, its deep-ranked matches, and
the application list; the only write is an application status transition, which goes
through the domain state machine (illegal jump → 409, unknown status → 422).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import (
    get_application_repository,
    get_job_repository,
    get_master_cv_repository,
)
from app.domain.applications import (
    APPLICATION_TRANSITIONS,
    Application,
    ApplicationRepository,
    ApplicationStatus,
    InvalidTransitionError,
)
from app.domain.cv import MasterCvRepository, StoredMasterCv
from app.domain.jobs import JobMatch, JobRepository

router = APIRouter(tags=["dashboard"])

ApplicationsDep = Annotated[ApplicationRepository, Depends(get_application_repository)]
JobsDep = Annotated[JobRepository, Depends(get_job_repository)]
MasterCvDep = Annotated[MasterCvRepository, Depends(get_master_cv_repository)]


# --- serialization -----------------------------------------------------------------


def _serialize_master_cv(stored: StoredMasterCv) -> dict[str, Any]:
    cv = stored.master_cv
    return {
        "user_id": stored.user_id,
        "version": stored.version,
        "created_at": stored.created_at.isoformat() if stored.created_at else None,
        "claim_count": len(cv.claims),
        "source_count": len(cv.sources),
        "claims": [
            {
                "problem": c.problem,
                "action": c.action,
                "result": c.result,
                "source_type": c.source_type,
                "source_ref": c.source_ref,
            }
            for c in cv.claims
        ],
        "sources": [
            {"source_type": s.source_type, "external_ref": s.external_ref, "title": s.title}
            for s in cv.sources
        ],
    }


def _serialize_match(match: JobMatch) -> dict[str, Any]:
    job = match.job
    return {
        "rank": match.rank,
        "score": match.score,
        "rationale": match.rationale,
        "matched_terms": list(match.matched_terms),
        "job": {
            "source": job.source,
            "external_id": job.external_id,
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "url": job.url,
            "remote": job.remote,
        },
    }


def _serialize_application(application: Application) -> dict[str, Any]:
    return {
        "id": application.id,
        "user_id": application.user_id,
        "status": application.status.value,
        "job_source": application.job_source,
        "job_external_id": application.job_external_id,
        "job_title": application.job_title,
        "job_company": application.job_company,
        "master_cv_version": application.master_cv_version,
        "materials": application.materials.to_json(),
        "allowed_transitions": sorted(
            status.value for status in APPLICATION_TRANSITIONS.get(application.status, frozenset())
        ),
    }


# --- routes --------------------------------------------------------------------------


@router.get("/master-cv/latest")
def latest_master_cv(user_id: str, repository: MasterCvDep) -> dict[str, Any]:
    """The user's current Master CV (claims + provenance), or 404 before first ingest."""
    stored = repository.get_latest(user_id)
    if stored is None:
        raise HTTPException(status_code=404, detail=f"no master CV for user '{user_id}'")
    return _serialize_master_cv(stored)


@router.get("/matches")
def list_matches(user_id: str, jobs: JobsDep, master_cvs: MasterCvDep) -> dict[str, Any]:
    """Deep-ranked matches for the user's latest Master CV version (empty before one exists)."""
    stored = master_cvs.get_latest(user_id)
    if stored is None:
        return {"master_cv_version": None, "matches": []}
    matches = jobs.get_matches(user_id, stored.version)
    return {
        "master_cv_version": stored.version,
        "matches": [_serialize_match(m) for m in matches],
    }


@router.get("/applications")
def list_applications(user_id: str, repository: ApplicationsDep) -> list[dict[str, Any]]:
    """All applications for the user, oldest first."""
    return [_serialize_application(a) for a in repository.list_applications(user_id)]


@router.get("/applications/{application_id}")
def get_application(application_id: int, repository: ApplicationsDep) -> dict[str, Any]:
    """One application with its tailored materials and outreach draft (if any)."""
    application = repository.get_application(application_id)
    if application is None:
        raise HTTPException(status_code=404, detail=f"no application with id {application_id}")
    outreach = repository.get_outreach_for_application(application_id)
    payload = _serialize_application(application)
    payload["outreach"] = (
        {
            "id": outreach.id,
            "status": outreach.status.value,
            "subject": outreach.subject,
            "body": outreach.body,
            "contact": (
                {
                    "name": outreach.contact.name,
                    "title": outreach.contact.title,
                    "email": outreach.contact.email,
                    "source": outreach.contact.source,
                }
                if outreach.contact
                else None
            ),
        }
        if outreach
        else None
    )
    return payload


@router.post("/applications/{application_id}/transition")
def transition_application(
    application_id: int, payload: dict[str, str], repository: ApplicationsDep
) -> dict[str, Any]:
    """Move an application through its state machine (e.g. mark it applied or ignored)."""
    raw_status = payload.get("status", "")
    try:
        new_status = ApplicationStatus(raw_status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"unknown status '{raw_status}'") from exc
    try:
        application = repository.transition_application(application_id, new_status)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _serialize_application(application)
