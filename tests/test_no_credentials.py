"""Prove the pipeline runs with zero real credentials, and the factory/MCP guardrails."""

from __future__ import annotations

import pytest
from app.config import Settings
from app.integrations.base import DriveConfigurationError
from app.integrations.drive_factory import create_drive_client
from app.integrations.mcp.drive import McpDriveClient
from app.integrations.mock.drive import MockDriveClient
from app.integrations.mock.github import MockGitHubClient
from app.services.claim_extraction import run_claim_extraction
from app.services.claim_repository import InMemoryClaimRepository

from tests.conftest import (
    APPROVED_FOLDER_ID,
    FIXTURES_DIR,
    GITHUB_FIXTURES_DIR,
    GITHUB_USERNAME,
)


def _offline_settings(**overrides: object) -> Settings:
    base = {
        "gdrive_mcp_enabled": False,
        "gdrive_source_folder_id": APPROVED_FOLDER_ID,
        "gdrive_mock_fixtures_dir": str(FIXTURES_DIR),
        "github_mcp_enabled": False,
        "github_username": GITHUB_USERNAME,
        "github_mock_fixtures_dir": str(GITHUB_FIXTURES_DIR),
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_factory_returns_mock_when_mcp_disabled() -> None:
    client = create_drive_client(_offline_settings())
    assert isinstance(client, MockDriveClient)


async def test_claim_extraction_runs_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ensure NO Drive/OAuth/Anthropic env vars are set — the run must not need them.
    for var in (
        "GDRIVE_MCP_SERVER",
        "GDRIVE_MCP_ENABLED",
        "GDRIVE_MCP_ACCESS_TOKEN",
        "ANTHROPIC_API_KEY",
        "DATABASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)

    settings = _offline_settings()
    repo = InMemoryClaimRepository()
    report = await run_claim_extraction(
        create_drive_client(settings),
        MockGitHubClient(GITHUB_FIXTURES_DIR),
        "user-1",
        repo,
        settings,
    )

    assert report.claims  # the V2 pipeline produced pending claims, fully offline
    assert repo.list_experiences("user-1")


def test_factory_returns_mcp_client_when_enabled() -> None:
    settings = _offline_settings(
        gdrive_mcp_enabled=True, gdrive_mcp_server="http://localhost:9000/mcp"
    )
    client = create_drive_client(settings)
    assert isinstance(client, McpDriveClient)


def test_mcp_client_requires_server_endpoint() -> None:
    settings = _offline_settings(gdrive_mcp_enabled=True, gdrive_mcp_server="")
    with pytest.raises(DriveConfigurationError):
        McpDriveClient(settings)


def test_mcp_client_requires_a_scope() -> None:
    settings = _offline_settings(
        gdrive_mcp_enabled=True,
        gdrive_mcp_server="http://localhost:9000/mcp",
        gdrive_source_folder_id="",
        gdrive_allow_broad_scan=False,
    )
    with pytest.raises(DriveConfigurationError):
        McpDriveClient(settings)


async def test_mcp_client_without_credentials_fails_clearly() -> None:
    # Config is valid, but no OAuth token/email is available (the encrypted store isn't
    # wired yet), so a real call must fail loudly rather than silently do nothing.
    settings = _offline_settings(
        gdrive_mcp_enabled=True, gdrive_mcp_server="http://localhost:9000/mcp"
    )
    client = McpDriveClient(settings)
    with pytest.raises(DriveConfigurationError):
        await client.read_source("drv_resume_001")
