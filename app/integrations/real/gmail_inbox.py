"""Gmail :class:`InboxScanner` — the scoped, read-only interview-detection read.

Uses the Gmail REST API: ``messages.list`` with the caller's query (Gmail's own search
syntax keeps the read scoped server-side), then ``messages.get`` with ``format=metadata``
plus the snippet for each hit. Deliberately shallow: headers + snippet are enough for
invite detection, and full bodies are never downloaded or stored. Requires the
``gmail.readonly`` scope on the stored Google credential.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from app.domain.interviews import InboxMessage
from app.integrations.base import InboxResponseError, MailCredentials

_DEFAULT_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
_MAX_RESULTS = 25


class GmailInboxScanner:
    """Scoped Gmail search over httpx (injectable for offline tests)."""

    def __init__(
        self,
        credentials: MailCredentials,
        client: httpx.AsyncClient | None = None,
        *,
        api_base: str = _DEFAULT_API_BASE,
    ) -> None:
        self._credentials = credentials
        self._client = client or httpx.AsyncClient(timeout=30.0)
        self._api_base = api_base

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._credentials.access_token}"}

    async def search_messages(
        self, query: str, since: datetime | None = None
    ) -> list[InboxMessage]:
        full_query = query
        if since is not None:
            full_query = f"{query} after:{int(since.timestamp())}"
        response = await self._client.get(
            f"{self._api_base}/messages",
            params={"q": full_query, "maxResults": _MAX_RESULTS},
            headers=self._headers(),
        )
        response.raise_for_status()
        refs = response.json().get("messages") or []

        messages: list[InboxMessage] = []
        for ref in refs:
            detail = await self._client.get(
                f"{self._api_base}/messages/{ref['id']}",
                params={
                    "format": "metadata",
                    "metadataHeaders": ["Subject", "From", "Date"],
                },
                headers=self._headers(),
            )
            detail.raise_for_status()
            messages.append(_map_message(detail.json()))
        return messages


def _map_message(payload: dict[str, Any]) -> InboxMessage:
    message_id = payload.get("id")
    if not message_id:
        raise InboxResponseError("Gmail message payload had no id.")
    headers = {
        str(h.get("name", "")).lower(): str(h.get("value", ""))
        for h in payload.get("payload", {}).get("headers", [])
    }
    received_at = None
    internal = payload.get("internalDate")
    if internal:
        received_at = datetime.fromtimestamp(int(internal) / 1000, tz=UTC)
    return InboxMessage(
        message_id=str(message_id),
        subject=headers.get("subject", ""),
        sender=headers.get("from", ""),
        body=str(payload.get("snippet", "")),
        received_at=received_at,
    )
