"""Sending approved outreach: state machine enforced, addresses never invented."""

from __future__ import annotations

import pytest
from app.domain.applications import OutreachStatus
from app.domain.jobs import Job
from app.domain.outreach import Contact, OutreachMessage
from app.domain.tailoring import TailoredMaterials
from app.integrations.base import MailSendError
from app.integrations.mock.mail import MockMailClient
from app.services.application_repository import InMemoryApplicationRepository
from app.services.outreach_send import send_approved_outreach, send_outreach

_CONTACT = Contact(name="Priya Raman", source="fixture", title="EM", email="priya@example.com")
_NO_EMAIL_CONTACT = Contact(name="Ana Sofia Duarte", source="fixture")


def _seed(
    repo: InMemoryApplicationRepository,
    external_id: str,
    contact: Contact | None,
    *,
    approve: bool = True,
) -> int:
    job = Job(
        source="mock", external_id=external_id, title="Engineer", company="ACME", description="x"
    )
    materials = TailoredMaterials(summary="fit", highlights=("h",), cover_letter="l")
    application = repo.get_or_create_application("u1", job, 1, materials)
    record = repo.upsert_draft(
        application.id, OutreachMessage(subject=f"re {external_id}", body="Hello,"), contact
    )
    if approve:
        repo.transition_outreach(record.id, OutreachStatus.APPROVED)
    return record.id


async def test_sends_approved_drafts_with_addresses() -> None:
    repo = InMemoryApplicationRepository()
    mail = MockMailClient()
    sent_id = _seed(repo, "j1", _CONTACT)

    report = await send_approved_outreach("u1", repo, mail)

    assert report.sent == [sent_id]
    assert [e.to for e in mail.outbox] == ["priya@example.com"]
    record = repo.get_outreach(sent_id)
    assert record is not None and record.status is OutreachStatus.SENT


async def test_drafted_records_are_never_sent() -> None:
    repo = InMemoryApplicationRepository()
    mail = MockMailClient()
    _seed(repo, "j1", _CONTACT, approve=False)  # still awaiting human approval

    report = await send_approved_outreach("u1", repo, mail)

    assert report.sent == []
    assert mail.outbox == []


async def test_no_address_skips_and_stays_approved() -> None:
    repo = InMemoryApplicationRepository()
    mail = MockMailClient()
    no_email = _seed(repo, "j1", _NO_EMAIL_CONTACT)
    no_contact = _seed(repo, "j2", None)

    report = await send_approved_outreach("u1", repo, mail)

    assert report.skipped_no_address == [no_email, no_contact]
    assert mail.outbox == []
    for outreach_id in (no_email, no_contact):
        record = repo.get_outreach(outreach_id)
        assert record is not None and record.status is OutreachStatus.APPROVED


async def test_one_failure_does_not_sink_the_batch() -> None:
    repo = InMemoryApplicationRepository()
    mail = MockMailClient(fail_times=1)  # first send fails, second succeeds
    first = _seed(repo, "j1", _CONTACT)
    second = _seed(repo, "j2", _CONTACT)

    report = await send_approved_outreach("u1", repo, mail)

    assert report.failed == [first]
    assert report.sent == [second]
    failed = repo.get_outreach(first)
    assert failed is not None and failed.status is OutreachStatus.APPROVED  # retryable


async def test_rerun_does_not_resend() -> None:
    repo = InMemoryApplicationRepository()
    mail = MockMailClient()
    _seed(repo, "j1", _CONTACT)

    await send_approved_outreach("u1", repo, mail)
    report = await send_approved_outreach("u1", repo, mail)

    assert report.sent == []
    assert len(mail.outbox) == 1  # exactly one real-world send, ever


async def test_send_outreach_requires_an_address() -> None:
    repo = InMemoryApplicationRepository()
    outreach_id = _seed(repo, "j1", None)
    record = repo.get_outreach(outreach_id)
    assert record is not None
    with pytest.raises(MailSendError, match="contact email"):
        await send_outreach(record, repo, MockMailClient())
