"""Master CV routes: the section picker, snapshot reads, and the rendered docx.

The V2 claim *review* queue (approve / edit-approve / reject) and the
``POST /master-cv/snapshots`` creation endpoint have been retired — story review
(``api/stories.py``) is the review unit now. What remains here is the read/render
surface: the experience section picker, reading the latest approved-claims snapshot
(old versions stay readable), and rendering + downloading the Master CV docx.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.requests import Request

from app.api.deps import get_artifact_store, get_claim_repository, get_snapshot_store
from app.config import Settings, get_settings
from app.domain.artifacts import KIND_MASTER_CV_DOCX, ArtifactStore
from app.domain.claims import (
    ClaimRepository,
    Experience,
    ExperienceSection,
)
from app.domain.master_cv_snapshot import MasterCvSnapshotStore, StoredSnapshot
from app.services.master_cv_render import (
    RenderConfigurationError,
    render_master_cv_artifact,
)

router = APIRouter(tags=["master-cv"])

RepositoryDep = Annotated[ClaimRepository, Depends(get_claim_repository)]
SnapshotStoreDep = Annotated[MasterCvSnapshotStore, Depends(get_snapshot_store)]
ArtifactStoreDep = Annotated[ArtifactStore, Depends(get_artifact_store)]


class LayoutRequest(BaseModel):
    """Section picker body: where an experience renders, and in what order."""

    section: ExperienceSection | None = None
    sort_order: int | None = Field(default=None, ge=0)


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


# --- master CV snapshots (read only) -----------------------------------------------------


@router.get("/master-cv/snapshots/latest")
def latest_snapshot(user_id: str, store: SnapshotStoreDep) -> dict[str, Any]:
    """The latest approved-claims Master CV version."""
    snapshot = store.get_latest(user_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"no Master CV snapshot for {user_id}")
    return _serialize_snapshot(snapshot)


# --- rendered docx (M11) --------------------------------------------------------------


def _request_settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


@router.post("/master-cv/render")
def render_master_cv(
    user_id: str,
    request: Request,
    repository: RepositoryDep,
    snapshot_store: SnapshotStoreDep,
    artifact_store: ArtifactStoreDep,
) -> dict[str, Any]:
    """Render the Master CV docx from approved claims (snapshot -> render -> artifact).

    503 when rendering is unconfigured (no profile/template) — a misconfiguration,
    never a resume with invented data. The frozen renderer's fidelity assertions run
    on every render; a violation is a 500, loudly.
    """
    try:
        artifact = render_master_cv_artifact(
            user_id, repository, snapshot_store, artifact_store, _request_settings(request)
        )
    except RenderConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "id": artifact.id,
        "kind": artifact.kind,
        "master_cv_version": artifact.master_cv_version,
        "file_path": artifact.file_path,
    }


@router.get("/master-cv/download")
def download_master_cv(user_id: str, artifact_store: ArtifactStoreDep) -> FileResponse:
    """Download the latest rendered Master CV docx."""
    artifact = artifact_store.get_latest(user_id, KIND_MASTER_CV_DOCX)
    if artifact is None:
        raise HTTPException(
            status_code=404, detail=f"no rendered Master CV for {user_id}; render one first"
        )
    path = Path(artifact.file_path)
    if not path.exists():
        raise HTTPException(
            status_code=404, detail=f"artifact file missing on disk: {artifact.file_path}"
        )
    version = f"_v{artifact.master_cv_version}" if artifact.master_cv_version else ""
    return FileResponse(
        path,
        media_type=("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        filename=f"master_cv{version}.docx",
    )
