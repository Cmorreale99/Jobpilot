"""GitHub file-universe acceptance tests (MASTER CV REPAIR spec §4.1/4.2/4.3, §16.1/16.3/16.4).

Source truth: the configured GitHub universe is the repository FILE TREE, not just the
root README + commits. Every enumerated file ends in an explicit disposition; README and
CLAUDE.md (root, nested — no exceptions) are captured as career evidence with path
provenance; a required-doc read failure marks the repo's ingestion incomplete and cannot
be masked by successful commit ingestion.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from app.config import Settings
from app.domain.claims import SOURCE_GITHUB_COMMIT, SOURCE_GITHUB_DOC, SOURCE_GITHUB_README
from app.integrations.base import DriveSource
from app.integrations.mock.github import MockGitHubClient
from app.services.roster import (
    GATHER_AWAITING_USER_DECISION,
    GATHER_OK,
    gather_source_documents,
)

FIXTURES = Path(__file__).parent / "fixtures" / "github_universe"
USER = "u-universe"


class EmptyDriveClient:
    """No Drive sources — these tests exercise the GitHub universe only."""

    async def list_candidate_sources(self, user_id: str) -> list[DriveSource]:
        return []

    async def read_source(self, source_ref: str):  # pragma: no cover - never called
        raise AssertionError("no drive sources exist")

    async def get_source_metadata(self, source_ref: str):  # pragma: no cover
        raise AssertionError("no drive sources exist")

    async def list_changed_sources(self, user_id: str, since: datetime):  # pragma: no cover
        return []


@pytest.fixture
def universe_settings(tmp_path: Path) -> Settings:
    return Settings(
        github_mcp_enabled=False,
        github_username="cmorreale",
        github_mock_fixtures_dir=str(FIXTURES),
        gdrive_source_folder_id="",
        gdrive_allow_broad_scan=False,
        uploads_dir="",
        database_url="sqlite+pysqlite:///:memory:",
    )


@pytest.fixture
def github_client() -> MockGitHubClient:
    return MockGitHubClient(FIXTURES)


async def _gather(github_client: MockGitHubClient, settings: Settings):
    return await gather_source_documents(EmptyDriveClient(), github_client, USER, settings)


# --- 16.1: file universe enumerated, every file dispositioned --------------------------


@pytest.mark.asyncio
async def test_client_enumerates_the_repository_file_tree(
    github_client: MockGitHubClient,
) -> None:
    files = await github_client.list_repo_files("cmorreale/jobpilot")
    paths = {f.path for f in files}
    assert paths == {
        "README.md",
        "CLAUDE.md",
        "docs/ARCHITECTURE.md",
        "app/main.py",
        "tests/test_main.py",
    }


@pytest.mark.asyncio
async def test_every_enumerated_file_has_an_explicit_disposition(
    github_client: MockGitHubClient, universe_settings: Settings
) -> None:
    gathered = await _gather(github_client, universe_settings)
    refs = {d.source_ref: d for d in gathered.report.dispositions}
    # Admitted docs are ingested with path provenance in the ref.
    for path in ("CLAUDE.md", "docs/ARCHITECTURE.md"):
        ref = f"cmorreale/jobpilot/{path}"
        assert ref in refs, f"no disposition for {ref}"
        assert refs[ref].status == GATHER_OK
    # Root README keeps its legacy identity (repo_ref) and is captured.
    assert refs["cmorreale/jobpilot"].status == GATHER_OK
    # Non-admitted files do NOT vanish: explicit awaiting-user-decision disposition.
    for path in ("app/main.py", "tests/test_main.py"):
        ref = f"cmorreale/jobpilot/{path}"
        assert ref in refs, f"non-admitted file silently disappeared: {ref}"
        assert refs[ref].status == GATHER_AWAITING_USER_DECISION
        assert refs[ref].reason


@pytest.mark.asyncio
async def test_nested_readmes_become_documents_with_path_provenance(
    github_client: MockGitHubClient, universe_settings: Settings
) -> None:
    gathered = await _gather(github_client, universe_settings)
    docs = {d.source_ref: d for d in gathered.documents}
    nested = "cmorreale/Cameron-Morreale-portfolio/projects/paper-recommender/README.md"
    assert nested in docs, "nested project README was not ingested"
    assert docs[nested].source_type == SOURCE_GITHUB_DOC
    assert "Paper Recommender System" in docs[nested].text
    assert "Restored date coverage across 500k papers" in docs[nested].text


@pytest.mark.asyncio
async def test_commits_counted_separately_from_files(
    github_client: MockGitHubClient, universe_settings: Settings
) -> None:
    gathered = await _gather(github_client, universe_settings)
    accounting = {a.repo_ref: a for a in gathered.report.repo_docs}
    jobpilot = accounting["cmorreale/jobpilot"]
    assert jobpilot.files_enumerated == 5
    assert jobpilot.commits_captured == 2
    # The file denominator never includes commits.
    assert jobpilot.docs_ingested == 3  # README.md, CLAUDE.md, docs/ARCHITECTURE.md


# --- 16.4: CLAUDE.md is discovered, captured, and reportable ---------------------------


@pytest.mark.asyncio
async def test_claude_md_is_captured_as_career_evidence(
    github_client: MockGitHubClient, universe_settings: Settings
) -> None:
    gathered = await _gather(github_client, universe_settings)
    docs = {d.source_ref: d for d in gathered.documents}
    ref = "cmorreale/jobpilot/CLAUDE.md"
    assert ref in docs, "CLAUDE.md was not ingested"
    assert docs[ref].source_type == SOURCE_GITHUB_DOC
    assert "every consequential claim traces to exact source" in docs[ref].text.lower()


@pytest.mark.asyncio
async def test_claude_md_only_repo_still_yields_document_evidence(
    github_client: MockGitHubClient, universe_settings: Settings
) -> None:
    """A repo whose strongest (only) evidence lives in CLAUDE.md must not be empty."""
    gathered = await _gather(github_client, universe_settings)
    docs = [d for d in gathered.documents if d.source_ref.startswith("cmorreale/claude-only")]
    assert docs, "CLAUDE.md-only repo produced zero documents"
    assert any("DAO governance" in d.text for d in docs)
    accounting = {a.repo_ref: a for a in gathered.report.repo_docs}
    assert accounting["cmorreale/claude-only"].claude_md_present is True
    assert accounting["cmorreale/claude-only"].claude_md_captured is True
    # README absence is reported, not an error.
    assert accounting["cmorreale/claude-only"].readme_present is False


# --- 16.3: README failure cannot be masked by commit success ---------------------------


@pytest.mark.asyncio
async def test_readme_failure_marks_repo_incomplete_despite_commit_success(
    github_client: MockGitHubClient, universe_settings: Settings
) -> None:
    gathered = await _gather(github_client, universe_settings)
    accounting = {a.repo_ref: a for a in gathered.report.repo_docs}
    broken = accounting["cmorreale/broken-readme"]
    assert broken.readme_present is True
    assert broken.readme_captured is False
    assert broken.commits_captured == 1  # history captured...
    assert broken.complete is False  # ...but ingestion is NOT complete
    # The failure is a named, required failure of the whole gather.
    assert any("broken-readme" in failure for failure in gathered.report.required_failures), (
        "README failure did not surface as a required-source failure"
    )
    assert gathered.report.complete is False


@pytest.mark.asyncio
async def test_gather_without_required_failures_is_complete(
    github_client: MockGitHubClient, universe_settings: Settings
) -> None:
    """Sanity inverse: repos whose required docs all captured are individually complete."""
    gathered = await _gather(github_client, universe_settings)
    accounting = {a.repo_ref: a for a in gathered.report.repo_docs}
    assert accounting["cmorreale/jobpilot"].complete is True
    assert accounting["cmorreale/Cameron-Morreale-portfolio"].complete is True


# --- commits remain gathered as before (supporting evidence, per repo) -----------------


@pytest.mark.asyncio
async def test_commit_documents_still_gathered(
    github_client: MockGitHubClient, universe_settings: Settings
) -> None:
    gathered = await _gather(github_client, universe_settings)
    commit_docs = [d for d in gathered.documents if d.source_type == SOURCE_GITHUB_COMMIT]
    assert {d.source_ref for d in commit_docs} >= {
        "cmorreale/jobpilot@aaa1",
        "cmorreale/jobpilot@aaa2",
        "cmorreale/broken-readme@ccc1",
    }


@pytest.mark.asyncio
async def test_root_readme_keeps_legacy_source_identity(
    github_client: MockGitHubClient, universe_settings: Settings
) -> None:
    """Root README rows keep (github_readme, repo_ref) so live evidence stays continuous."""
    gathered = await _gather(github_client, universe_settings)
    readmes = [d for d in gathered.documents if d.source_type == SOURCE_GITHUB_README]
    assert {d.source_ref for d in readmes} == {
        "cmorreale/jobpilot",
        "cmorreale/Cameron-Morreale-portfolio",
    }
