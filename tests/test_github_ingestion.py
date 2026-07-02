"""GitHub ingestion into the Master CV: provenance, signals, combined build."""

from __future__ import annotations

from app.config import Settings
from app.integrations.mock.drive import MockDriveClient
from app.integrations.mock.github import MockGitHubClient
from app.services.master_cv_ingestion import build_master_cv, ingest_github_sources


async def test_ingest_records_github_provenance(
    mock_github_client: MockGitHubClient, settings: Settings
) -> None:
    sources = await ingest_github_sources(mock_github_client, "user-1", settings)
    assert {s.external_ref for s in sources} == {
        "jordanrivera/fraud-stream",
        "jordanrivera/payments-ledger",
    }
    for source in sources:
        assert source.source_type == "github"
        assert source.mime_type == "text/markdown"
        assert source.raw_text.strip()
        # The factual contribution-signals footer is appended to the README.
        assert "## Repository signals" in source.raw_text
        assert source.ingested_at is not None


async def test_github_signals_are_factual(
    mock_github_client: MockGitHubClient, settings: Settings
) -> None:
    sources = await ingest_github_sources(mock_github_client, "user-1", settings)
    ledger = next(s for s in sources if s.external_ref == "jordanrivera/payments-ledger")
    assert "Commits: 511" in ledger.raw_text
    assert "Languages: Go, SQL" in ledger.raw_text


async def test_combined_master_cv_merges_drive_and_github(
    mock_client: MockDriveClient,
    mock_github_client: MockGitHubClient,
    settings: Settings,
) -> None:
    cv = await build_master_cv(mock_client, mock_github_client, "user-1", settings)

    source_types = {s.source_type for s in cv.sources}
    assert source_types == {"gdrive", "github"}
    assert cv.claims

    # Every claim still traces to a source; GitHub-derived claims trace to a repo_ref.
    refs = {s.external_ref for s in cv.sources}
    assert all(c.source_ref in refs for c in cv.claims)
    github_claims = [c for c in cv.claims if c.source_ref == "jordanrivera/fraud-stream"]
    assert github_claims
    assert any(c.problem and c.result for c in github_claims)
