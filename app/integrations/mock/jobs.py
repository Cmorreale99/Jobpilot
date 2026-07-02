"""Fixture-backed :class:`JobSource`.

Reads job postings from a local JSON file so the matching pipeline runs offline. The
``since`` filter mirrors a real "recent jobs" query.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.domain.jobs import Job

_JOBS_FILE = "jobs.json"


def _parse_time(value: object) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class MockJobSource:
    """Serves job postings from ``<fixtures_dir>/jobs.json``."""

    def __init__(self, fixtures_dir: str | Path) -> None:
        self._path = Path(fixtures_dir) / _JOBS_FILE

    def _load(self) -> list[Job]:
        if not self._path.exists():
            raise FileNotFoundError(f"Mock jobs fixture not found at {self._path}.")
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        return [
            Job(
                source=str(entry.get("source", "mock")),
                external_id=str(entry["external_id"]),
                title=str(entry["title"]),
                company=str(entry["company"]),
                description=str(entry.get("description", "")),
                location=entry.get("location"),
                url=entry.get("url"),
                posted_at=_parse_time(entry.get("posted_at")),
                remote=bool(entry.get("remote", False)),
            )
            for entry in raw["jobs"]
        ]

    async def fetch_recent_jobs(self, since: datetime | None = None) -> list[Job]:
        jobs = self._load()
        if since is None:
            return jobs
        return [j for j in jobs if j.posted_at is not None and j.posted_at > since]
