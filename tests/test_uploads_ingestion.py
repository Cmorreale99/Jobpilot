"""Uploads: local client, format policy, and gathering into the V2 evidence path."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.config import Settings
from app.domain.claims import SOURCE_UPLOAD
from app.integrations.base import UploadsConfigurationError
from app.integrations.mock.drive import MockDriveClient
from app.integrations.mock.github import MockGitHubClient
from app.integrations.uploads import LocalUploadsClient, create_uploads_client
from app.services.roster import gather_source_documents

from tests.conftest import (
    APPROVED_FOLDER_ID,
    FIXTURES_DIR,
    GITHUB_FIXTURES_DIR,
    GITHUB_USERNAME,
)

UPLOADS_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "uploads"


@pytest.fixture
def uploads_client() -> LocalUploadsClient:
    return LocalUploadsClient(UPLOADS_FIXTURES_DIR)


@pytest.fixture
def uploads_settings() -> Settings:
    return Settings(
        uploads_dir=str(UPLOADS_FIXTURES_DIR),
        gdrive_source_folder_id=APPROVED_FOLDER_ID,
        gdrive_mock_fixtures_dir=str(FIXTURES_DIR),
        github_username=GITHUB_USERNAME,
        github_mock_fixtures_dir=str(GITHUB_FIXTURES_DIR),
    )


# --- client --------------------------------------------------------------------------


async def test_client_lists_files_with_mime_and_mtime(
    uploads_client: LocalUploadsClient,
) -> None:
    candidates = await uploads_client.list_candidate_uploads()
    by_ref = {c.upload_ref: c for c in candidates}
    assert set(by_ref) == {"consulting_engagement.md", "award_letter.txt", "resume_scan.pdf"}
    assert by_ref["consulting_engagement.md"].mime_type == "text/markdown"
    assert by_ref["award_letter.txt"].mime_type == "text/plain"
    assert by_ref["resume_scan.pdf"].mime_type == "application/octet-stream"
    assert all(c.modified_time is not None for c in candidates)


async def test_client_reads_text(uploads_client: LocalUploadsClient) -> None:
    document = await uploads_client.read_upload("award_letter.txt")
    assert "Engineering Excellence Award" in document.text
    assert document.title == "award letter"


async def test_client_missing_file_raises(uploads_client: LocalUploadsClient) -> None:
    with pytest.raises(FileNotFoundError):
        await uploads_client.read_upload("nope.txt")


async def test_client_missing_directory_fails_loudly() -> None:
    client = LocalUploadsClient("does/not/exist")
    with pytest.raises(UploadsConfigurationError):
        await client.list_candidate_uploads()


def test_factory_returns_none_when_unconfigured() -> None:
    assert create_uploads_client(Settings(uploads_dir="")) is None
    assert create_uploads_client(Settings(uploads_dir="  ")) is None


# --- policy + V2 gathering ---------------------------------------------------------------


async def test_gathering_applies_format_policy_and_normalizes(
    uploads_settings: Settings,
) -> None:
    documents = await gather_source_documents(
        MockDriveClient(FIXTURES_DIR),
        MockGitHubClient(GITHUB_FIXTURES_DIR),
        "u1",
        uploads_settings,
    )
    uploads = [d for d in documents if d.source_type == SOURCE_UPLOAD]
    refs = {d.source_ref for d in uploads}
    assert refs == {"consulting_engagement.md", "award_letter.txt"}  # .pdf excluded
    assert all(d.text for d in uploads)
    # All three source families feed the one V2 gathering path.
    assert {d.source_type for d in documents} > {SOURCE_UPLOAD}


async def test_uploads_skipped_entirely_when_unconfigured(settings: Settings) -> None:
    documents = await gather_source_documents(
        MockDriveClient(FIXTURES_DIR),
        MockGitHubClient(GITHUB_FIXTURES_DIR),
        "u1",
        settings,  # conftest settings: uploads_dir empty
    )
    assert all(d.source_type != SOURCE_UPLOAD for d in documents)
