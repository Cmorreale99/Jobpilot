"""Application + outreach repositories: idempotency, human decisions preserved (memory + SQL)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from app.db.application_repository import SqlApplicationRepository
from app.db.session import create_all, create_session_factory
from app.domain.applications import (
    ApplicationRepository,
    ApplicationStatus,
    InvalidTransitionError,
    OutreachStatus,
)
from app.domain.jobs import Job
from app.domain.outreach import Contact, OutreachMessage
from app.domain.tailoring import TailoredMaterials
from app.services.application_repository import InMemoryApplicationRepository


def _job(external_id: str = "j1", title: str = "Engineer") -> Job:
    return Job(source="mock", external_id=external_id, title=title, company="ACME", description="x")


def _materials(summary: str = "fit") -> TailoredMaterials:
    return TailoredMaterials(summary=summary, highlights=("did a thing",), cover_letter="letter")


def _message(subject: str = "hello") -> OutreachMessage:
    return OutreachMessage(subject=subject, body="body")


_CONTACT = Contact(name="Priya Raman", source="fixture", title="EM", email="p@example.com")


@pytest.fixture(params=["memory", "sql"])
def repo(request: pytest.FixtureRequest) -> Iterator[ApplicationRepository]:
    if request.param == "memory":
        yield InMemoryApplicationRepository()
    else:
        engine = sa.create_engine("sqlite+pysqlite:///:memory:")
        create_all(engine)
        yield SqlApplicationRepository(create_session_factory(engine))


# --- applications --------------------------------------------------------------------


def test_create_application_starts_drafted(repo: ApplicationRepository) -> None:
    application = repo.get_or_create_application("u1", _job(), 1, _materials())
    assert application.status is ApplicationStatus.DRAFTED
    assert application.job_ref == ("mock", "j1")
    assert application.materials == _materials()


def test_get_or_create_is_idempotent_and_refreshes_drafts(repo: ApplicationRepository) -> None:
    first = repo.get_or_create_application("u1", _job(), 1, _materials("v1"))
    second = repo.get_or_create_application("u1", _job(), 2, _materials("v2"))
    assert second.id == first.id  # same application, not a duplicate
    assert second.master_cv_version == 2
    assert second.materials.summary == "v2"
    assert len(repo.list_applications("u1")) == 1


def test_acted_on_application_is_never_refreshed(repo: ApplicationRepository) -> None:
    application = repo.get_or_create_application("u1", _job(), 1, _materials("v1"))
    repo.transition_application(application.id, ApplicationStatus.APPLIED)
    again = repo.get_or_create_application("u1", _job(), 2, _materials("v2"))
    assert again.status is ApplicationStatus.APPLIED
    assert again.materials.summary == "v1"  # applied-with materials preserved
    assert again.master_cv_version == 1


def test_application_transition_validates_state_machine(repo: ApplicationRepository) -> None:
    application = repo.get_or_create_application("u1", _job(), 1, _materials())
    with pytest.raises(InvalidTransitionError):
        repo.transition_application(application.id, ApplicationStatus.OFFER)
    assert (
        repo.transition_application(application.id, ApplicationStatus.APPLIED).status
        is ApplicationStatus.APPLIED
    )


def test_transition_missing_application_raises_lookup(repo: ApplicationRepository) -> None:
    with pytest.raises(LookupError):
        repo.transition_application(999, ApplicationStatus.APPLIED)


def test_applications_are_per_user(repo: ApplicationRepository) -> None:
    repo.get_or_create_application("u1", _job(), 1, _materials())
    other = repo.get_or_create_application("u2", _job(), 1, _materials())
    assert other.status is ApplicationStatus.DRAFTED
    assert len(repo.list_applications("u1")) == 1
    assert len(repo.list_applications("u2")) == 1


# --- outreach ------------------------------------------------------------------------


def test_upsert_draft_round_trips_contact(repo: ApplicationRepository) -> None:
    application = repo.get_or_create_application("u1", _job(), 1, _materials())
    record = repo.upsert_draft(application.id, _message(), _CONTACT)
    assert record.status is OutreachStatus.DRAFTED
    assert record.contact == _CONTACT
    fetched = repo.get_outreach(record.id)
    assert fetched == record


def test_upsert_draft_without_contact(repo: ApplicationRepository) -> None:
    application = repo.get_or_create_application("u1", _job(), 1, _materials())
    record = repo.upsert_draft(application.id, _message(), None)
    assert record.contact is None


def test_upsert_draft_replaces_drafted_content_without_duplicating(
    repo: ApplicationRepository,
) -> None:
    application = repo.get_or_create_application("u1", _job(), 1, _materials())
    first = repo.upsert_draft(application.id, _message("v1"), None)
    second = repo.upsert_draft(application.id, _message("v2"), _CONTACT)
    assert second.id == first.id
    assert second.subject == "v2"
    assert len(repo.list_pending_outreach("u1")) == 1


def test_upsert_never_overwrites_a_human_decision(repo: ApplicationRepository) -> None:
    application = repo.get_or_create_application("u1", _job(), 1, _materials())
    record = repo.upsert_draft(application.id, _message("v1"), None)
    repo.transition_outreach(record.id, OutreachStatus.APPROVED)
    again = repo.upsert_draft(application.id, _message("v2"), _CONTACT)
    assert again.subject == "v1"  # approved content untouched
    assert again.status is OutreachStatus.APPROVED


def test_upsert_draft_for_missing_application_raises(repo: ApplicationRepository) -> None:
    with pytest.raises(LookupError):
        repo.upsert_draft(999, _message(), None)


def test_pending_queue_only_lists_drafted(repo: ApplicationRepository) -> None:
    a1 = repo.get_or_create_application("u1", _job("j1"), 1, _materials())
    a2 = repo.get_or_create_application("u1", _job("j2"), 1, _materials())
    r1 = repo.upsert_draft(a1.id, _message(), None)
    repo.upsert_draft(a2.id, _message(), None)
    repo.transition_outreach(r1.id, OutreachStatus.DISCARDED)
    pending = repo.list_pending_outreach("u1")
    assert [r.application_id for r in pending] == [a2.id]


def test_outreach_transition_validates_state_machine(repo: ApplicationRepository) -> None:
    application = repo.get_or_create_application("u1", _job(), 1, _materials())
    record = repo.upsert_draft(application.id, _message(), None)
    with pytest.raises(InvalidTransitionError):
        repo.transition_outreach(record.id, OutreachStatus.SENT)  # must be approved first
    approved = repo.transition_outreach(record.id, OutreachStatus.APPROVED)
    assert approved.status is OutreachStatus.APPROVED
