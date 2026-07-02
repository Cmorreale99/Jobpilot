"""Remotive job source: offline response mapping, HTML flattening, since filter, factory."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from app.config import Settings
from app.integrations.jobs_factory import create_job_source
from app.integrations.mock.jobs import MockJobSource
from app.integrations.real.jobs_remotive import RemotiveJobSource

_PAYLOAD = {
    "jobs": [
        {
            "id": 42001,
            "title": "Senior Backend Engineer, Payments",
            "company_name": "Ledgerline",
            "description": "<p>Build <b>idempotent</b> settlement workers over Kafka &amp; Go.</p>",
            "url": "https://remotive.com/jobs/42001",
            "publication_date": "2026-07-01T08:00:00",
            "candidate_required_location": "Worldwide",
        },
        {
            "id": 42002,
            "title": "Marketing Manager",
            "company_name": "Glowbrand",
            "description": "<div>Own our brand campaigns.</div>",
            "url": "https://remotive.com/jobs/42002",
            "publication_date": "2026-06-01T08:00:00",
            "candidate_required_location": "",
        },
    ]
}


def _source(handler: httpx.MockTransport, **kwargs: object) -> RemotiveJobSource:
    return RemotiveJobSource(
        httpx.AsyncClient(transport=handler),
        api_url="https://remotive.test/api/remote-jobs",
        **kwargs,  # type: ignore[arg-type]
    )


async def test_maps_payload_to_domain_jobs() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_PAYLOAD)

    jobs = await _source(httpx.MockTransport(handler)).fetch_recent_jobs()

    assert [j.external_id for j in jobs] == ["42001", "42002"]
    first = jobs[0]
    assert first.source == "remotive"
    assert first.title == "Senior Backend Engineer, Payments"
    assert first.company == "Ledgerline"
    assert first.location == "Worldwide"
    assert first.remote is True
    assert first.posted_at == datetime(2026, 7, 1, 8, 0, tzinfo=UTC)
    assert jobs[1].location is None  # empty string -> None


async def test_html_is_flattened_for_the_tokenizer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_PAYLOAD)

    (first, _) = await _source(httpx.MockTransport(handler)).fetch_recent_jobs()
    assert "<" not in first.description
    assert "idempotent settlement workers over Kafka & Go." in first.description


async def test_since_filters_older_postings() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_PAYLOAD)

    jobs = await _source(httpx.MockTransport(handler)).fetch_recent_jobs(
        since=datetime(2026, 6, 15, tzinfo=UTC)
    )
    assert [j.external_id for j in jobs] == ["42001"]


async def test_search_and_limit_are_passed_to_the_api() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"jobs": []})

    await _source(
        httpx.MockTransport(handler), search="backend payments", limit=50
    ).fetch_recent_jobs()
    assert seen == {"search": "backend payments", "limit": "50"}


async def test_http_error_propagates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with pytest.raises(httpx.HTTPStatusError):
        await _source(httpx.MockTransport(handler)).fetch_recent_jobs()


# --- factory -------------------------------------------------------------------------------


def test_factory_defaults_to_mock() -> None:
    assert isinstance(create_job_source(Settings()), MockJobSource)


def test_factory_selects_remotive() -> None:
    source = create_job_source(Settings(job_source_provider="remotive"))
    assert isinstance(source, RemotiveJobSource)


def test_factory_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="JOB_SOURCE_PROVIDER"):
        create_job_source(Settings(job_source_provider="linkedin"))
