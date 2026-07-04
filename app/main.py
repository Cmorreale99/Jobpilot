"""FastAPI application entrypoint.

``uvicorn app.main:app`` serves the API. The app imports with **zero credentials**: the
OAuth flow is built lazily on first use (or injected in tests), so nothing here requires
a real key or database at import time.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.claims import router as claims_router
from app.api.dashboard import router as dashboard_router
from app.api.interviews import router as interviews_router
from app.api.oauth import router as oauth_router
from app.api.outreach import router as outreach_router
from app.config import Settings, get_settings
from app.domain.applications import ApplicationRepository
from app.domain.claims import ClaimRepository
from app.domain.cv import MasterCvRepository
from app.domain.interviews import InterviewRepository
from app.domain.jobs import JobRepository
from app.domain.master_cv_snapshot import MasterCvSnapshotStore
from app.integrations.base import MailClient
from app.services.oauth_flow import OAuthFlowService


def create_app(
    *,
    settings: Settings | None = None,
    flow: OAuthFlowService | None = None,
    application_repository: ApplicationRepository | None = None,
    job_repository: JobRepository | None = None,
    master_cv_repository: MasterCvRepository | None = None,
    mail_client: MailClient | None = None,
    interview_repository: InterviewRepository | None = None,
    claim_repository: ClaimRepository | None = None,
    snapshot_store: MasterCvSnapshotStore | None = None,
) -> FastAPI:
    """Build the FastAPI app. Tests inject mock-backed services; prod builds them lazily."""
    app = FastAPI(title="JobPilot", version="0.1.0")
    app.state.settings = settings or get_settings()
    app.state.flow = flow
    app.state.application_repository = application_repository
    app.state.job_repository = job_repository
    app.state.master_cv_repository = master_cv_repository
    app.state.mail_client = mail_client
    app.state.interview_repository = interview_repository
    app.state.claim_repository = claim_repository
    app.state.snapshot_store = snapshot_store
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(app.state.settings.dashboard_origin_list),
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(oauth_router)
    app.include_router(outreach_router)
    app.include_router(dashboard_router)
    app.include_router(interviews_router)
    app.include_router(claims_router)

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
