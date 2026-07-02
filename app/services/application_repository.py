"""In-memory :class:`ApplicationRepository` for mock-first tests and offline runs."""

from __future__ import annotations

from dataclasses import replace

from app.domain.applications import (
    APPLICATION_TRANSITIONS,
    OUTREACH_TRANSITIONS,
    Application,
    ApplicationStatus,
    OutreachRecord,
    OutreachStatus,
    refresh_application,
    validate_transition,
)
from app.domain.jobs import Job
from app.domain.outreach import Contact, OutreachMessage
from app.domain.tailoring import TailoredMaterials


class InMemoryApplicationRepository:
    """Non-persistent application + outreach store with the same semantics as the SQL one."""

    def __init__(self) -> None:
        self._applications: dict[int, Application] = {}
        self._by_job: dict[tuple[str, str, str], int] = {}  # (user_id, source, external_id) -> id
        self._outreach: dict[int, OutreachRecord] = {}
        self._outreach_by_application: dict[int, int] = {}
        self._next_application_id = 1
        self._next_outreach_id = 1

    # --- applications ------------------------------------------------------------

    def get_or_create_application(
        self, user_id: str, job: Job, master_cv_version: int, materials: TailoredMaterials
    ) -> Application:
        key = (user_id, job.source, job.external_id)
        existing_id = self._by_job.get(key)
        if existing_id is not None:
            updated = refresh_application(
                self._applications[existing_id], master_cv_version, materials
            )
            self._applications[existing_id] = updated
            return updated
        application = Application(
            id=self._next_application_id,
            user_id=user_id,
            job_source=job.source,
            job_external_id=job.external_id,
            job_title=job.title,
            job_company=job.company,
            master_cv_version=master_cv_version,
            status=ApplicationStatus.DRAFTED,
            materials=materials,
        )
        self._next_application_id += 1
        self._applications[application.id] = application
        self._by_job[key] = application.id
        return application

    def get_application(self, application_id: int) -> Application | None:
        return self._applications.get(application_id)

    def list_applications(self, user_id: str) -> list[Application]:
        return sorted(
            (a for a in self._applications.values() if a.user_id == user_id),
            key=lambda a: a.id,
        )

    def transition_application(
        self, application_id: int, new_status: ApplicationStatus
    ) -> Application:
        application = self._applications.get(application_id)
        if application is None:
            raise LookupError(f"no application with id {application_id}")
        validate_transition(APPLICATION_TRANSITIONS, application.status, new_status)
        updated = replace(application, status=new_status)
        self._applications[application_id] = updated
        return updated

    # --- outreach ------------------------------------------------------------------

    def upsert_draft(
        self, application_id: int, message: OutreachMessage, contact: Contact | None
    ) -> OutreachRecord:
        application = self._applications.get(application_id)
        if application is None:
            raise LookupError(f"no application with id {application_id}")
        existing_id = self._outreach_by_application.get(application_id)
        if existing_id is not None:
            existing = self._outreach[existing_id]
            if existing.status is not OutreachStatus.DRAFTED:
                return existing  # a human decision is never overwritten
            updated = replace(existing, subject=message.subject, body=message.body, contact=contact)
            self._outreach[existing_id] = updated
            return updated
        record = OutreachRecord(
            id=self._next_outreach_id,
            application_id=application_id,
            user_id=application.user_id,
            subject=message.subject,
            body=message.body,
            status=OutreachStatus.DRAFTED,
            contact=contact,
        )
        self._next_outreach_id += 1
        self._outreach[record.id] = record
        self._outreach_by_application[application_id] = record.id
        return record

    def get_outreach(self, outreach_id: int) -> OutreachRecord | None:
        return self._outreach.get(outreach_id)

    def get_outreach_for_application(self, application_id: int) -> OutreachRecord | None:
        outreach_id = self._outreach_by_application.get(application_id)
        return self._outreach.get(outreach_id) if outreach_id is not None else None

    def list_pending_outreach(self, user_id: str) -> list[OutreachRecord]:
        return sorted(
            (
                o
                for o in self._outreach.values()
                if o.user_id == user_id and o.status is OutreachStatus.DRAFTED
            ),
            key=lambda o: o.id,
        )

    def transition_outreach(self, outreach_id: int, new_status: OutreachStatus) -> OutreachRecord:
        record = self._outreach.get(outreach_id)
        if record is None:
            raise LookupError(f"no outreach with id {outreach_id}")
        validate_transition(OUTREACH_TRANSITIONS, record.status, new_status)
        updated = replace(record, status=new_status)
        self._outreach[outreach_id] = updated
        return updated
