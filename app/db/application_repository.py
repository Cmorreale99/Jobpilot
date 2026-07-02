"""SQLAlchemy-backed :class:`ApplicationRepository`.

Same semantics as the in-memory implementation: one application per ``(user_id, job)``,
at most one outreach row per application, drafted rows refreshed idempotently, and rows
a human has acted on never overwritten by the pipeline. Status changes validate against
the domain state machines before they touch the database.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ApplicationRow, OutreachRow
from app.domain.applications import (
    APPLICATION_TRANSITIONS,
    OUTREACH_TRANSITIONS,
    Application,
    ApplicationStatus,
    OutreachRecord,
    OutreachStatus,
    validate_transition,
)
from app.domain.jobs import Job
from app.domain.outreach import Contact, OutreachMessage
from app.domain.tailoring import TailoredMaterials


def _to_application(row: ApplicationRow) -> Application:
    return Application(
        id=row.id,
        user_id=row.user_id,
        job_source=row.job_source,
        job_external_id=row.job_external_id,
        job_title=row.job_title,
        job_company=row.job_company,
        master_cv_version=row.master_cv_version,
        status=ApplicationStatus(row.status),
        materials=TailoredMaterials.from_json(row.materials_json),
    )


def _to_outreach(row: OutreachRow) -> OutreachRecord:
    contact = (
        Contact(
            name=row.contact_name,
            source=row.contact_source or "",
            title=row.contact_title,
            email=row.contact_email,
        )
        if row.contact_name
        else None
    )
    return OutreachRecord(
        id=row.id,
        application_id=row.application_id,
        user_id=row.user_id,
        subject=row.subject,
        body=row.body,
        status=OutreachStatus(row.status),
        contact=contact,
    )


class SqlApplicationRepository:
    """Application + outreach persistence over a SQLAlchemy session factory."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    # --- applications ------------------------------------------------------------

    def get_or_create_application(
        self, user_id: str, job: Job, master_cv_version: int, materials: TailoredMaterials
    ) -> Application:
        with self._session_factory() as session:
            row = session.scalar(
                select(ApplicationRow).where(
                    ApplicationRow.user_id == user_id,
                    ApplicationRow.job_source == job.source,
                    ApplicationRow.job_external_id == job.external_id,
                )
            )
            if row is None:
                row = ApplicationRow(
                    user_id=user_id,
                    job_source=job.source,
                    job_external_id=job.external_id,
                    job_title=job.title,
                    job_company=job.company,
                    master_cv_version=master_cv_version,
                    status=ApplicationStatus.DRAFTED.value,
                    materials_json=materials.to_json(),
                )
                session.add(row)
            elif ApplicationStatus(row.status) is ApplicationStatus.DRAFTED:
                # Idempotent refresh: re-tailor drafts, never touch acted-on applications.
                row.master_cv_version = master_cv_version
                row.materials_json = materials.to_json()
            session.commit()
            session.refresh(row)
            return _to_application(row)

    def get_application(self, application_id: int) -> Application | None:
        with self._session_factory() as session:
            row = session.get(ApplicationRow, application_id)
            return _to_application(row) if row is not None else None

    def list_applications(self, user_id: str) -> list[Application]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(ApplicationRow)
                .where(ApplicationRow.user_id == user_id)
                .order_by(ApplicationRow.id)
            )
            return [_to_application(row) for row in rows]

    def transition_application(
        self, application_id: int, new_status: ApplicationStatus
    ) -> Application:
        with self._session_factory() as session:
            row = session.get(ApplicationRow, application_id)
            if row is None:
                raise LookupError(f"no application with id {application_id}")
            validate_transition(APPLICATION_TRANSITIONS, ApplicationStatus(row.status), new_status)
            row.status = new_status.value
            session.commit()
            session.refresh(row)
            return _to_application(row)

    # --- outreach ------------------------------------------------------------------

    def upsert_draft(
        self, application_id: int, message: OutreachMessage, contact: Contact | None
    ) -> OutreachRecord:
        with self._session_factory() as session:
            application = session.get(ApplicationRow, application_id)
            if application is None:
                raise LookupError(f"no application with id {application_id}")
            row = session.scalar(
                select(OutreachRow).where(OutreachRow.application_id == application_id)
            )
            if row is not None and OutreachStatus(row.status) is not OutreachStatus.DRAFTED:
                return _to_outreach(row)  # a human decision is never overwritten
            if row is None:
                row = OutreachRow(
                    application_id=application_id,
                    user_id=application.user_id,
                    status=OutreachStatus.DRAFTED.value,
                    subject="",
                    body="",
                )
                session.add(row)
            row.subject = message.subject
            row.body = message.body
            row.contact_name = contact.name if contact else None
            row.contact_title = contact.title if contact else None
            row.contact_email = contact.email if contact else None
            row.contact_source = contact.source if contact else None
            session.commit()
            session.refresh(row)
            return _to_outreach(row)

    def get_outreach(self, outreach_id: int) -> OutreachRecord | None:
        with self._session_factory() as session:
            row = session.get(OutreachRow, outreach_id)
            return _to_outreach(row) if row is not None else None

    def get_outreach_for_application(self, application_id: int) -> OutreachRecord | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(OutreachRow).where(OutreachRow.application_id == application_id)
            )
            return _to_outreach(row) if row is not None else None

    def list_pending_outreach(self, user_id: str) -> list[OutreachRecord]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(OutreachRow)
                .where(
                    OutreachRow.user_id == user_id,
                    OutreachRow.status == OutreachStatus.DRAFTED.value,
                )
                .order_by(OutreachRow.id)
            )
            return [_to_outreach(row) for row in rows]

    def transition_outreach(self, outreach_id: int, new_status: OutreachStatus) -> OutreachRecord:
        with self._session_factory() as session:
            row = session.get(OutreachRow, outreach_id)
            if row is None:
                raise LookupError(f"no outreach with id {outreach_id}")
            validate_transition(OUTREACH_TRANSITIONS, OutreachStatus(row.status), new_status)
            row.status = new_status.value
            session.commit()
            session.refresh(row)
            return _to_outreach(row)
