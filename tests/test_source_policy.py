"""Policy tests: broad scan off by default, unsupported MIME types skipped."""

from __future__ import annotations

from app.config import Settings
from app.integrations.mock.drive import MockDriveClient
from app.services.source_policy import apply_source_policy


async def test_broad_scan_disabled_by_default(
    mock_client: MockDriveClient, settings: Settings
) -> None:
    assert settings.gdrive_allow_broad_scan is False
    raw = await mock_client.list_candidate_sources("user-1")
    allowed = apply_source_policy(raw, settings)
    refs = {s.source_ref for s in allowed}
    # The personal-folder journal must NOT be ingested when broad scan is off.
    assert "drv_journal_006" not in refs
    assert all(s.folder_id == "career_docs" for s in allowed)


async def test_unsupported_mime_types_are_skipped(
    mock_client: MockDriveClient, settings: Settings
) -> None:
    raw = await mock_client.list_candidate_sources("user-1")
    allowed = apply_source_policy(raw, settings)
    refs = {s.source_ref for s in allowed}
    # The image/jpeg headshot is in the approved folder but not an allowed MIME type.
    assert "drv_headshot_005" not in refs
    assert all(s.mime_type in settings.gdrive_allowed_mime_types_set for s in allowed)


async def test_allowed_career_artifacts_pass(
    mock_client: MockDriveClient, settings: Settings
) -> None:
    raw = await mock_client.list_candidate_sources("user-1")
    refs = {s.source_ref for s in apply_source_policy(raw, settings)}
    assert refs == {
        "drv_resume_001",
        "drv_project_002",
        "drv_portfolio_003",
        "drv_transcript_004",
    }


async def test_broad_scan_still_respects_mime_allowlist(
    mock_client: MockDriveClient, settings: Settings
) -> None:
    broad = settings.model_copy(update={"gdrive_allow_broad_scan": True})
    raw = await mock_client.list_candidate_sources("user-1")
    refs = {s.source_ref for s in apply_source_policy(raw, broad)}
    # Broad scan now pulls the personal journal (text/plain) ...
    assert "drv_journal_006" in refs
    # ... but the image is still skipped by MIME policy.
    assert "drv_headshot_005" not in refs


async def test_no_scope_configured_ingests_nothing() -> None:
    from tests.conftest import FIXTURES_DIR

    settings = Settings(
        gdrive_source_folder_id="",
        gdrive_allow_broad_scan=False,
        gdrive_mock_fixtures_dir=str(FIXTURES_DIR),
    )
    client = MockDriveClient(FIXTURES_DIR)
    raw = await client.list_candidate_sources("user-1")
    # No approved folder and no broad scan => never scan the whole Drive.
    assert apply_source_policy(raw, settings) == []
