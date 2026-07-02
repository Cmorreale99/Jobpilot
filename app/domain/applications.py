"""Applications and the outreach approval queue: state machines + persistence contract.

Both ``applications.status`` and ``outreach.status`` are **explicit state machines with
validated transitions** (per CLAUDE.md). Every status change anywhere in the app goes
through :func:`validate_transition`, so an illegal jump (e.g. re-drafting a sent message)
raises instead of silently corrupting state. Repositories enforce the complementary
idempotency rule: nightly re-runs may refresh *drafted* rows but never touch rows a
human has acted on.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol, runtime_checkable

from app.domain.jobs import Job
from app.domain.outreach import Contact, OutreachMessage
from app.domain.tailoring import TailoredMaterials


class InvalidTransitionError(ValueError):
    """Raised on a status change the state machine does not allow."""


class ApplicationStatus(StrEnum):
    """Lifecycle of one application (``applications.status``)."""

    DRAFTED = "drafted"
    APPLIED = "applied"
    INTERVIEWING = "interviewing"
    REJECTED = "rejected"
    OFFER = "offer"
    IGNORED = "ignored"


class OutreachStatus(StrEnum):
    """Lifecycle of one outreach draft through the approval queue (``outreach.status``)."""

    DRAFTED = "drafted"
    APPROVED = "approved"
    SENT = "sent"
    DISCARDED = "discarded"


# Allowed transitions. Terminal states map to the empty set. ``rejected``/``offer``/
# ``ignored`` and ``sent``/``discarded`` are terminal on purpose: reopening is a new
# application/draft, not a status edit.
APPLICATION_TRANSITIONS: dict[ApplicationStatus, frozenset[ApplicationStatus]] = {
    ApplicationStatus.DRAFTED: frozenset({ApplicationStatus.APPLIED, ApplicationStatus.IGNORED}),
    ApplicationStatus.APPLIED: frozenset(
        {ApplicationStatus.INTERVIEWING, ApplicationStatus.REJECTED, ApplicationStatus.OFFER}
    ),
    ApplicationStatus.INTERVIEWING: frozenset(
        {ApplicationStatus.REJECTED, ApplicationStatus.OFFER}
    ),
    ApplicationStatus.REJECTED: frozenset(),
    ApplicationStatus.OFFER: frozenset(),
    ApplicationStatus.IGNORED: frozenset(),
}

OUTREACH_TRANSITIONS: dict[OutreachStatus, frozenset[OutreachStatus]] = {
    OutreachStatus.DRAFTED: frozenset({OutreachStatus.APPROVED, OutreachStatus.DISCARDED}),
    OutreachStatus.APPROVED: frozenset({OutreachStatus.SENT}),
    OutreachStatus.SENT: frozenset(),
    OutreachStatus.DISCARDED: frozenset(),
}


def validate_transition[S: StrEnum](transitions: dict[S, frozenset[S]], current: S, new: S) -> None:
    """Raise :class:`InvalidTransitionError` unless ``current -> new`` is allowed."""
    if new not in transitions.get(current, frozenset()):
        raise InvalidTransitionError(f"cannot transition from '{current}' to '{new}'")


@dataclass(frozen=True)
class Application:
    """One application to one job, carrying its tailored materials.

    ``job_title``/``job_company`` are denormalized for display; the full posting lives in
    ``jobs`` keyed by ``(job_source, job_external_id)``.
    """

    id: int
    user_id: str
    job_source: str
    job_external_id: str
    job_title: str
    job_company: str
    master_cv_version: int
    status: ApplicationStatus
    materials: TailoredMaterials

    @property
    def job_ref(self) -> tuple[str, str]:
        return (self.job_source, self.job_external_id)


@dataclass(frozen=True)
class OutreachRecord:
    """One outreach draft in the approval queue, tied to its application."""

    id: int
    application_id: int
    user_id: str
    subject: str
    body: str
    status: OutreachStatus
    contact: Contact | None = None


def refresh_application(
    application: Application, master_cv_version: int, materials: TailoredMaterials
) -> Application:
    """Re-tailor a *drafted* application in place; any other status is returned untouched.

    This is the shared idempotency rule both repository implementations apply on
    ``get_or_create_application``: a nightly re-run refreshes drafts but never clobbers
    an application the user has already acted on.
    """
    if application.status is not ApplicationStatus.DRAFTED:
        return application
    return replace(application, master_cv_version=master_cv_version, materials=materials)


@runtime_checkable
class ApplicationRepository(Protocol):
    """Persistence for applications and their outreach drafts (the approval queue).

    Write semantics shared by every implementation:

    * ``get_or_create_application`` is **idempotent** per ``(user_id, job ref)``: an
      existing *drafted* application has its materials refreshed; an existing
      non-drafted one is returned unchanged.
    * ``upsert_draft`` keeps at most one outreach row per application: a *drafted* row
      is replaced with the new content; an approved/sent/discarded row is returned
      unchanged (a human decision is never overwritten by the pipeline).
    * ``transition_*`` validate against the state machines and raise
      :class:`InvalidTransitionError` on an illegal change, :class:`LookupError` on a
      missing id.
    """

    def get_or_create_application(
        self, user_id: str, job: Job, master_cv_version: int, materials: TailoredMaterials
    ) -> Application: ...

    def get_application(self, application_id: int) -> Application | None: ...

    def list_applications(self, user_id: str) -> list[Application]: ...

    def transition_application(
        self, application_id: int, new_status: ApplicationStatus
    ) -> Application: ...

    def upsert_draft(
        self, application_id: int, message: OutreachMessage, contact: Contact | None
    ) -> OutreachRecord: ...

    def get_outreach(self, outreach_id: int) -> OutreachRecord | None: ...

    def get_outreach_for_application(self, application_id: int) -> OutreachRecord | None: ...

    def list_pending_outreach(self, user_id: str) -> list[OutreachRecord]: ...

    def transition_outreach(
        self, outreach_id: int, new_status: OutreachStatus
    ) -> OutreachRecord: ...
