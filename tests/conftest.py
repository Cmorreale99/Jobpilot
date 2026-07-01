"""Shared test fixtures for the Drive / Master CV slice.

All fixtures run fully offline: no env vars, no credentials, no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.config import Settings
from app.integrations.mock.drive import MockDriveClient

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "drive"
APPROVED_FOLDER_ID = "career_docs"


@pytest.fixture
def drive_fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def settings() -> Settings:
    """Safe-path settings scoped to the fixture career-docs folder.

    Constructed explicitly (not from the environment) so tests never depend on a real
    ``.env`` or credentials.
    """
    return Settings(
        gdrive_mcp_enabled=False,
        gdrive_source_folder_id=APPROVED_FOLDER_ID,
        gdrive_mock_fixtures_dir=str(FIXTURES_DIR),
        gdrive_allow_broad_scan=False,
    )


@pytest.fixture
def mock_client(drive_fixtures_dir: Path) -> MockDriveClient:
    return MockDriveClient(drive_fixtures_dir)
