"""Remotive :class:`JobSource` — the compliant real job API.

Remotive publishes remote job postings through a public, documented, keyless JSON API
(https://remotive.com/api/remote-jobs) that explicitly permits listing reuse — the
compliant alternative to scraping. Descriptions arrive as HTML; they are flattened to
plain text so the matching tokenizer sees words, not tags.
"""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime

import httpx

from app.domain.jobs import Job

_TAG_RE = re.compile(r"<[^>]+>")

SOURCE_NAME = "remotive"


def _strip_html(text: str) -> str:
    return " ".join(html.unescape(_TAG_RE.sub(" ", text)).split())


def _parse_time(value: object) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class RemotiveJobSource:
    """Fetches recent postings from the Remotive public API."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        api_url: str,
        search: str = "",
        limit: int = 200,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=30.0)
        self._api_url = api_url
        self._search = search
        self._limit = limit

    async def fetch_recent_jobs(self, since: datetime | None = None) -> list[Job]:
        params: dict[str, str | int] = {"limit": self._limit}
        if self._search:
            params["search"] = self._search
        response = await self._client.get(self._api_url, params=params)
        response.raise_for_status()
        jobs = [self._to_job(entry) for entry in response.json().get("jobs", [])]
        if since is None:
            return jobs
        return [j for j in jobs if j.posted_at is not None and j.posted_at > since]

    @staticmethod
    def _to_job(entry: dict[str, object]) -> Job:
        return Job(
            source=SOURCE_NAME,
            external_id=str(entry["id"]),
            title=str(entry.get("title", "")),
            company=str(entry.get("company_name", "")),
            description=_strip_html(str(entry.get("description", ""))),
            location=str(entry.get("candidate_required_location", "")) or None,
            url=str(entry.get("url", "")) or None,
            posted_at=_parse_time(entry.get("publication_date")),
            remote=True,  # Remotive lists remote roles exclusively
        )
