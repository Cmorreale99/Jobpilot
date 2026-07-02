"""Unit tests for the fixture-backed MockGitHubClient."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.integrations.base import GitHubClient, GitHubDocument, GitHubRepo
from app.integrations.mock.github import MockGitHubClient


def test_mock_client_satisfies_interface(mock_github_client: MockGitHubClient) -> None:
    assert isinstance(mock_github_client, GitHubClient)


async def test_list_candidate_repos_returns_all_fixtures(
    mock_github_client: MockGitHubClient,
) -> None:
    repos = await mock_github_client.list_candidate_repos("user-1")
    refs = {r.repo_ref for r in repos}
    # Raw listing includes forks/private/other-owner — policy filtering happens later.
    assert {"jordanrivera/fraud-stream", "jordanrivera/awesome-lists-fork"} <= refs
    assert all(isinstance(r, GitHubRepo) for r in repos)


async def test_read_repo_returns_readme(mock_github_client: MockGitHubClient) -> None:
    doc = await mock_github_client.read_repo("jordanrivera/fraud-stream")
    assert isinstance(doc, GitHubDocument)
    assert doc.repo_ref == "jordanrivera/fraud-stream"
    assert "Realtime card-fraud scoring" in doc.text


async def test_get_repo_metadata_has_signals(mock_github_client: MockGitHubClient) -> None:
    meta = await mock_github_client.get_repo_metadata("jordanrivera/payments-ledger")
    assert meta.primary_language == "Go"
    assert "Go" in meta.languages
    assert meta.commit_count == 511
    assert meta.stars == 57


async def test_list_changed_repos_filters_by_time(
    mock_github_client: MockGitHubClient,
) -> None:
    since = datetime(2026, 6, 1, tzinfo=UTC)
    changed = {r.repo_ref for r in await mock_github_client.list_changed_repos("user-1", since)}
    assert "jordanrivera/fraud-stream" in changed  # pushed 2026-06-10
    assert "jordanrivera/payments-ledger" not in changed  # pushed 2026-05-22


async def test_unknown_repo_ref_raises(mock_github_client: MockGitHubClient) -> None:
    with pytest.raises(KeyError):
        await mock_github_client.read_repo("nobody/nope")
