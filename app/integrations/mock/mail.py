"""In-memory :class:`MailClient` — the mock-first default. Nothing leaves the process."""

from __future__ import annotations

from app.integrations.base import MailSendError, OutgoingEmail


class MockMailClient:
    """Records sends to an inspectable outbox; can be scripted to fail."""

    def __init__(self, *, fail_times: int = 0) -> None:
        self.outbox: list[OutgoingEmail] = []
        self._remaining_failures = fail_times

    async def send_email(self, email: OutgoingEmail) -> str:
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise MailSendError("mock mail client: simulated send failure")
        self.outbox.append(email)
        return f"mock-msg-{len(self.outbox)}"
