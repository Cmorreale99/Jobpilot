"""Fixture-backed :class:`ResearchClient`.

Looks contacts up by company name in ``<fixtures_dir>/contacts.json``, so outreach
drafting runs offline. Companies deliberately absent from the fixture exercise the
no-contact path — the honest ``None`` a real research client must also return.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.domain.jobs import Job
from app.domain.outreach import Contact

_CONTACTS_FILE = "contacts.json"


class MockResearchClient:
    """Serves researched contacts from ``<fixtures_dir>/contacts.json``."""

    def __init__(self, fixtures_dir: str | Path) -> None:
        self._path = Path(fixtures_dir) / _CONTACTS_FILE

    def _load(self) -> dict[str, Contact]:
        if not self._path.exists():
            raise FileNotFoundError(f"Mock contacts fixture not found at {self._path}.")
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        contacts: dict[str, Contact] = {}
        for entry in raw["contacts"]:
            contacts[str(entry["company"]).casefold()] = Contact(
                name=str(entry["name"]),
                source=str(entry.get("source", "mock-fixture")),
                title=entry.get("title"),
                email=entry.get("email"),
            )
        return contacts

    async def find_contact(self, job: Job) -> Contact | None:
        return self._load().get(job.company.casefold())
