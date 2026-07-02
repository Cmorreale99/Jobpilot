"""Gmail :class:`MailClient` — real sends, only ever reached behind approval.

Uses the Gmail REST API (``users.messages.send``) with the user's OAuth token from the
encrypted credential store. The Google credential must carry the ``gmail.send`` scope
(add ``https://www.googleapis.com/auth/gmail.send`` to ``GOOGLE_OAUTH_SCOPES`` before
connecting the account). Send-only by design: this client cannot read the mailbox.
"""

from __future__ import annotations

import base64
from email.message import EmailMessage

import httpx

from app.integrations.base import MailCredentials, MailSendError, OutgoingEmail

_DEFAULT_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


class GmailMailClient:
    """Sends one RFC 822 message per approved draft via the Gmail API."""

    def __init__(
        self,
        credentials: MailCredentials,
        client: httpx.AsyncClient | None = None,
        *,
        send_url: str = _DEFAULT_SEND_URL,
    ) -> None:
        self._credentials = credentials
        self._client = client or httpx.AsyncClient(timeout=30.0)
        self._send_url = send_url

    def _encode(self, email: OutgoingEmail) -> str:
        message = EmailMessage()
        message["To"] = email.to
        message["From"] = self._credentials.sender_email
        message["Subject"] = email.subject
        message.set_content(email.body)
        return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")

    async def send_email(self, email: OutgoingEmail) -> str:
        try:
            response = await self._client.post(
                self._send_url,
                json={"raw": self._encode(email)},
                headers={"Authorization": f"Bearer {self._credentials.access_token}"},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise MailSendError(f"Gmail send failed: {exc}") from exc
        message_id = response.json().get("id")
        if not message_id:
            raise MailSendError("Gmail send response did not include a message id.")
        return str(message_id)
