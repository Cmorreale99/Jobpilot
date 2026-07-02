"""Sending approved outreach — the only place a message can leave the pipeline.

Sends go through the state machine (``approved → sent``), so nothing that is merely
drafted or already discarded can ever be sent. Three per-draft outcomes:

* **sent** — a researched contact email existed and the mail client accepted it.
* **skipped** — no contact email on record. The draft stays approved; we never guess
  or invent an address.
* **failed** — the mail client errored. The draft stays approved so the next run (or a
  manual send) retries; one failure never sinks the batch.

With the default ``MockMailClient`` nothing leaves the process; real sends additionally
require ``GMAIL_ENABLED=true`` plus a stored Google credential with the gmail.send scope.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.domain.applications import (
    ApplicationRepository,
    OutreachRecord,
    OutreachStatus,
)
from app.integrations.base import MailClient, MailSendError, OutgoingEmail

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SendReport:
    """What one send pass did (for logging, tests, and the pipeline summary)."""

    sent: list[int] = field(default_factory=list)  # outreach ids
    skipped_no_address: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)


async def send_outreach(
    record: OutreachRecord, repository: ApplicationRepository, mail_client: MailClient
) -> OutreachRecord:
    """Send one *approved* draft and mark it sent.

    Raises :class:`InvalidTransitionError` via the repository if the record is not in a
    sendable state, and :class:`MailSendError` if it has no address or the send fails —
    in both failure cases the record's status is untouched.
    """
    if record.contact is None or not record.contact.email:
        raise MailSendError(
            "No researched contact email on record for this draft; it cannot be sent."
        )
    message_id = await mail_client.send_email(
        OutgoingEmail(to=record.contact.email, subject=record.subject, body=record.body)
    )
    logger.info("outreach %d sent (provider message id %s)", record.id, message_id)
    return repository.transition_outreach(record.id, OutreachStatus.SENT)


async def send_approved_outreach(
    user_id: str,
    repository: ApplicationRepository,
    mail_client: MailClient,
) -> SendReport:
    """Send every approved draft that has a contact address; report the rest."""
    report = SendReport()
    for record in repository.list_outreach_by_status(user_id, OutreachStatus.APPROVED):
        if record.contact is None or not record.contact.email:
            logger.info("outreach %d stays approved: no contact email on record", record.id)
            report.skipped_no_address.append(record.id)
            continue
        try:
            await send_outreach(record, repository, mail_client)
        except MailSendError as exc:
            logger.warning("outreach %d send failed (stays approved): %s", record.id, exc)
            report.failed.append(record.id)
            continue
        report.sent.append(record.id)
    return report
