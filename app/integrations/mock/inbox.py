"""Fixture-backed :class:`InboxScanner`.

Serves emails from ``<fixtures_dir>/messages.json`` so interview detection runs
offline. The mock honors the query the same way a real scoped search would — only
messages containing a query term are returned — so tests exercise the data-minimization
path, not just the parsing.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.domain.interviews import InboxMessage

_MESSAGES_FILE = "messages.json"


def _parse_time(value: object) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _query_terms(query: str) -> list[str]:
    return [t.strip().strip('"').lower() for t in query.split(" OR ") if t.strip()]


class MockInboxScanner:
    """Serves messages from ``<fixtures_dir>/messages.json``, filtered by query terms."""

    def __init__(self, fixtures_dir: str | Path) -> None:
        self._path = Path(fixtures_dir) / _MESSAGES_FILE

    def _load(self) -> list[InboxMessage]:
        if not self._path.exists():
            raise FileNotFoundError(f"Mock inbox fixture not found at {self._path}.")
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        return [
            InboxMessage(
                message_id=str(entry["message_id"]),
                subject=str(entry.get("subject", "")),
                sender=str(entry.get("sender", "")),
                body=str(entry.get("body", "")),
                received_at=_parse_time(entry.get("received_at")),
            )
            for entry in raw["messages"]
        ]

    async def search_messages(
        self, query: str, since: datetime | None = None
    ) -> list[InboxMessage]:
        terms = _query_terms(query)
        results = []
        for message in self._load():
            haystack = f"{message.subject} {message.body}".lower()
            if terms and not any(term in haystack for term in terms):
                continue
            if since is not None and (message.received_at is None or message.received_at <= since):
                continue
            results.append(message)
        return results
