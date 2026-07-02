"""FastAPI dependency wiring for the OAuth and outreach routes.

The :class:`OAuthFlowService` must be a **singleton** across requests because the
in-memory state store links a ``/start`` to its later ``/callback``. It is either
injected at app construction (tests supply a mock-backed flow) or built lazily on first
use from settings and cached on ``app.state``. The application repository follows the
same pattern: injected in tests (in-memory), built lazily over SQL in prod.
"""

from __future__ import annotations

from fastapi import HTTPException
from starlette.requests import Request

from app.config import Settings, get_settings
from app.db.application_repository import SqlApplicationRepository
from app.db.credentials_store import SqlOAuthCredentialStore
from app.db.interview_repository import SqlInterviewRepository
from app.db.job_repository import SqlJobRepository
from app.db.master_cv_repository import SqlMasterCvRepository
from app.db.session import create_all, create_db_engine, create_session_factory
from app.domain.applications import ApplicationRepository
from app.domain.cv import MasterCvRepository
from app.domain.interviews import InterviewRepository
from app.domain.jobs import JobRepository
from app.integrations.base import MailClient, MailConfigurationError
from app.integrations.mail_factory import create_mail_client
from app.integrations.oauth.base import OAuthError
from app.integrations.oauth.factory import create_oauth_providers
from app.security.crypto import TokenCipher, TokenEncryptionError
from app.services.oauth_flow import OAuthFlowService


def build_default_flow(settings: Settings) -> OAuthFlowService:
    """Assemble the production flow: encrypted SQL store + real providers.

    ``create_all`` is a dev convenience so the table exists without a migration step;
    production schema is owned by Alembic (see PLAN.md).
    """
    cipher = TokenCipher.from_settings(settings)
    engine = create_db_engine(settings)
    create_all(engine)
    store = SqlOAuthCredentialStore(create_session_factory(engine), cipher)
    return OAuthFlowService(store, create_oauth_providers(settings))


def get_flow(request: Request) -> OAuthFlowService:
    """Return the app's OAuth flow, building a default one on first use.

    Returns HTTP 503 (not a 500) when OAuth cannot be configured — e.g. a missing
    ``TOKEN_ENCRYPTION_KEY`` — so the misconfiguration is reported clearly.
    """
    existing = getattr(request.app.state, "flow", None)
    if isinstance(existing, OAuthFlowService):
        return existing
    settings = getattr(request.app.state, "settings", None) or get_settings()
    try:
        flow = build_default_flow(settings)
    except (TokenEncryptionError, OAuthError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=f"OAuth is not configured: {exc}") from exc
    request.app.state.flow = flow
    return flow


def build_default_application_repository(settings: Settings) -> SqlApplicationRepository:
    """Assemble the production application repository over the configured database.

    ``create_all`` is a dev convenience so the tables exist without a migration step;
    production schema is owned by Alembic (see PLAN.md).
    """
    engine = create_db_engine(settings)
    create_all(engine)
    return SqlApplicationRepository(create_session_factory(engine))


def get_application_repository(request: Request) -> ApplicationRepository:
    """Return the app's application repository, building a default one on first use."""
    existing = getattr(request.app.state, "application_repository", None)
    if isinstance(existing, ApplicationRepository):
        return existing
    settings = getattr(request.app.state, "settings", None) or get_settings()
    repository = build_default_application_repository(settings)
    request.app.state.application_repository = repository
    return repository


def get_job_repository(request: Request) -> JobRepository:
    """Return the app's job repository, building a SQL-backed one on first use."""
    existing = getattr(request.app.state, "job_repository", None)
    if isinstance(existing, JobRepository):
        return existing
    settings = getattr(request.app.state, "settings", None) or get_settings()
    engine = create_db_engine(settings)
    create_all(engine)
    repository = SqlJobRepository(create_session_factory(engine))
    request.app.state.job_repository = repository
    return repository


def get_mail_client(request: Request) -> MailClient:
    """Return the app's mail client, building the configured one on first use.

    Mock (in-process outbox) unless ``GMAIL_ENABLED``; misconfiguration of the real
    client (missing encryption key or credential) is reported as HTTP 503.
    """
    existing = getattr(request.app.state, "mail_client", None)
    if isinstance(existing, MailClient):
        return existing
    settings = getattr(request.app.state, "settings", None) or get_settings()
    store = None
    try:
        if settings.gmail_enabled:
            cipher = TokenCipher.from_settings(settings)
            engine = create_db_engine(settings)
            create_all(engine)
            store = SqlOAuthCredentialStore(create_session_factory(engine), cipher)
        client = create_mail_client(settings, store=store, user_id=settings.pipeline_user_id)
    except (TokenEncryptionError, MailConfigurationError) as exc:
        raise HTTPException(status_code=503, detail=f"Mail is not configured: {exc}") from exc
    request.app.state.mail_client = client
    return client


def get_interview_repository(request: Request) -> InterviewRepository:
    """Return the app's interview repository, building a SQL-backed one on first use."""
    existing = getattr(request.app.state, "interview_repository", None)
    if isinstance(existing, InterviewRepository):
        return existing
    settings = getattr(request.app.state, "settings", None) or get_settings()
    engine = create_db_engine(settings)
    create_all(engine)
    repository = SqlInterviewRepository(create_session_factory(engine))
    request.app.state.interview_repository = repository
    return repository


def get_master_cv_repository(request: Request) -> MasterCvRepository:
    """Return the app's Master CV repository, building a SQL-backed one on first use."""
    existing = getattr(request.app.state, "master_cv_repository", None)
    if isinstance(existing, MasterCvRepository):
        return existing
    settings = getattr(request.app.state, "settings", None) or get_settings()
    engine = create_db_engine(settings)
    create_all(engine)
    repository = SqlMasterCvRepository(create_session_factory(engine))
    request.app.state.master_cv_repository = repository
    return repository
