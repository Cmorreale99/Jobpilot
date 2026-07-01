"""Unit tests for the fixture-backed MockDriveClient."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.integrations.base import DriveClient, DriveDocument, DriveSource
from app.integrations.mock.drive import MockDriveClient


def test_mock_client_satisfies_interface(mock_client: MockDriveClient) -> None:
    assert isinstance(mock_client, DriveClient)


async def test_list_candidate_sources_returns_all_fixtures(mock_client: MockDriveClient) -> None:
    sources = await mock_client.list_candidate_sources("user-1")
    refs = {s.source_ref for s in sources}
    # Raw listing includes everything in the manifest — policy filtering happens later.
    assert {"drv_resume_001", "drv_project_002", "drv_portfolio_003"} <= refs
    assert all(isinstance(s, DriveSource) for s in sources)


async def test_read_source_returns_extracted_text(mock_client: MockDriveClient) -> None:
    doc = await mock_client.read_source("drv_resume_001")
    assert isinstance(doc, DriveDocument)
    assert doc.source_ref == "drv_resume_001"
    assert doc.mime_type == "text/plain"
    assert "Northwind Payments" in doc.text


async def test_get_source_metadata_has_no_body(mock_client: MockDriveClient) -> None:
    meta = await mock_client.get_source_metadata("drv_project_002")
    assert meta.source_ref == "drv_project_002"
    assert meta.mime_type == "text/markdown"
    assert meta.size_bytes is not None and meta.size_bytes > 0


async def test_list_changed_sources_filters_by_time(mock_client: MockDriveClient) -> None:
    since = datetime(2026, 6, 1, tzinfo=UTC)
    changed = {s.source_ref for s in await mock_client.list_changed_sources("user-1", since)}
    # Only the portfolio (2026-06-20) and journal (2026-06-28) were modified after June 1.
    assert "drv_portfolio_003" in changed
    assert "drv_resume_001" not in changed


async def test_unknown_source_ref_raises(mock_client: MockDriveClient) -> None:
    with pytest.raises(KeyError):
        await mock_client.read_source("does_not_exist")
