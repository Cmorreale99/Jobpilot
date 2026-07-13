"""Coverage-denominator acceptance tests (MASTER CV REPAIR §4.16, §5.1.6-8, §16.15).

Source truth: the denominator is the actual configured source universe. Discovery
coverage (was the universe enumerated?) and processing coverage (how much of the
admitted universe was captured?) are separate numbers; a repository with two of ten
admitted files captured is 20% processed, never "fully ingested", and the eight
missing files are named.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from app.config import Settings
from app.integrations.base import (
    GitHubCommit,
    GitHubDocument,
    GitHubRepo,
    GitHubRepoFile,
    GitHubResponseError,
)
from app.services.roster import gather_source_documents

from tests.test_github_universe import EmptyDriveClient

USER = "u-coverage"
REPO = "cmorreale/ten-docs"


class TenFileGitHubClient:
    """Ten admitted Markdown files; only two read successfully."""

    readable = {"docs/one.md", "docs/two.md"}

    async def list_candidate_repos(self, user_id: str) -> list[GitHubRepo]:
        return [GitHubRepo(repo_ref=REPO, name="ten-docs", owner="cmorreale")]

    async def list_repo_files(self, repo_ref: str) -> list[GitHubRepoFile]:
        return [
            GitHubRepoFile(repo_ref=repo_ref, path=f"docs/{name}.md")
            for name in ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
        ] + [GitHubRepoFile(repo_ref=repo_ref, path="notes.md")]

    async def read_repo(self, repo_ref: str) -> GitHubDocument:  # pragma: no cover
        raise GitHubResponseError("no root README")

    async def read_repo_file(self, repo_ref: str, path: str) -> GitHubDocument:
        if path in self.readable:
            return GitHubDocument(repo_ref=repo_ref, title=path, text=f"# {path}", path=path)
        raise GitHubResponseError(f"simulated read failure for {path}")

    async def list_commits(self, repo_ref: str) -> list[GitHubCommit]:
        return []

    async def list_changed_repos(self, user_id: str, since: datetime) -> list[GitHubRepo]:
        return []


def _settings() -> Settings:
    return Settings(
        github_mcp_enabled=False,
        github_username="cmorreale",
        gdrive_source_folder_id="",
        uploads_dir="",
    )


@pytest.mark.asyncio
async def test_processing_coverage_uses_the_configured_universe_denominator() -> None:
    gathered = await gather_source_documents(
        EmptyDriveClient(), TenFileGitHubClient(), USER, _settings()
    )
    coverage = gathered.report.coverage()
    repo_cov = next(c for c in coverage["repositories"] if c["repo_ref"] == REPO)

    # Discovery: the universe WAS enumerated — 10 files.
    assert repo_cov["files_enumerated"] == 10
    assert repo_cov["discovery_complete"] is True
    # Processing: 2 of 10 admitted docs captured = 20%, not 100%.
    assert repo_cov["docs_admitted"] == 10
    assert repo_cov["docs_ingested"] == 2
    assert repo_cov["processing_pct"] == pytest.approx(20.0)
    assert repo_cov["fully_ingested"] is False
    # The eight missing files are NAMED.
    assert len(repo_cov["missing_files"]) == 8
    assert any("docs/three.md" in ref for ref in repo_cov["missing_files"])


@pytest.mark.asyncio
async def test_totals_separate_discovery_from_processing() -> None:
    gathered = await gather_source_documents(
        EmptyDriveClient(), TenFileGitHubClient(), USER, _settings()
    )
    totals = gathered.report.coverage()["totals"]
    assert totals["repositories_discovered"] == 1
    assert totals["files_enumerated"] == 10
    assert totals["docs_admitted"] == 10
    assert totals["docs_ingested"] == 2
    assert totals["read_failures"] == 8
