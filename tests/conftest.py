"""Shared test fixtures for the Drive / Master CV slice.

All fixtures run fully offline: no env vars, no credentials, no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.config import Settings
from app.integrations.mock.drive import MockDriveClient
from app.integrations.mock.github import MockGitHubClient
from app.integrations.mock.jobs import MockJobSource
from app.integrations.mock.research import MockResearchClient

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "drive"
GITHUB_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "github"
JOBS_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "jobs"
RESEARCH_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "research"
INBOX_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "inbox"
APPROVED_FOLDER_ID = "career_docs"
GITHUB_USERNAME = "jordanrivera"


@pytest.fixture(autouse=True)
def _ignore_developer_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests hermetic: never read the developer's real ``.env``.

    Every ``Settings(...)`` constructed in tests sees only explicit kwargs and real
    env vars — a filled-in local ``.env`` (real tokens, enabled integrations) must
    not change test behavior.
    """
    monkeypatch.setattr(
        Settings, "model_config", {**Settings.model_config, "env_file": None}, raising=True
    )


@pytest.fixture
def drive_fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def github_fixtures_dir() -> Path:
    return GITHUB_FIXTURES_DIR


@pytest.fixture
def settings() -> Settings:
    """Safe-path settings scoped to the fixture career-docs folder and GitHub account.

    Constructed explicitly (not from the environment) so tests never depend on a real
    ``.env`` or credentials.
    """
    return Settings(
        gdrive_mcp_enabled=False,
        gdrive_source_folder_id=APPROVED_FOLDER_ID,
        gdrive_mock_fixtures_dir=str(FIXTURES_DIR),
        gdrive_allow_broad_scan=False,
        github_mcp_enabled=False,
        github_username=GITHUB_USERNAME,
        github_mock_fixtures_dir=str(GITHUB_FIXTURES_DIR),
        github_allow_broad_scan=False,
        jobs_mock_fixtures_dir=str(JOBS_FIXTURES_DIR),
        research_mock_fixtures_dir=str(RESEARCH_FIXTURES_DIR),
        inbox_mock_fixtures_dir=str(INBOX_FIXTURES_DIR),
    )


@pytest.fixture
def mock_client(drive_fixtures_dir: Path) -> MockDriveClient:
    return MockDriveClient(drive_fixtures_dir)


@pytest.fixture
def mock_github_client(github_fixtures_dir: Path) -> MockGitHubClient:
    return MockGitHubClient(github_fixtures_dir)


@pytest.fixture
def mock_job_source() -> MockJobSource:
    return MockJobSource(JOBS_FIXTURES_DIR)


@pytest.fixture
def mock_research_client() -> MockResearchClient:
    return MockResearchClient(RESEARCH_FIXTURES_DIR)
