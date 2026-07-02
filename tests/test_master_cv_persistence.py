"""End-to-end: ingest from mocks and persist the Master CV (versioned, idempotent)."""

from __future__ import annotations

import sqlalchemy as sa
from app.config import Settings
from app.db.master_cv_repository import SqlMasterCvRepository
from app.db.session import create_all, create_session_factory
from app.integrations.mock.drive import MockDriveClient
from app.integrations.mock.github import MockGitHubClient
from app.services.master_cv_ingestion import refresh_master_cv
from app.services.master_cv_repository import InMemoryMasterCvRepository


async def test_refresh_persists_and_is_idempotent(
    mock_client: MockDriveClient,
    mock_github_client: MockGitHubClient,
    settings: Settings,
) -> None:
    repo = InMemoryMasterCvRepository()

    first = await refresh_master_cv(mock_client, mock_github_client, "u1", repo, settings)
    assert first.version == 1
    # Evidence spans both sources.
    source_types = {s.source_type for s in first.master_cv.sources}
    assert source_types == {"gdrive", "github"}
    assert first.master_cv.claims

    # A second run with unchanged fixtures must not create a new version.
    second = await refresh_master_cv(mock_client, mock_github_client, "u1", repo, settings)
    assert second.version == 1
    assert repo.list_versions("u1") == [1]


async def test_refresh_into_sql_dedupes_sources(
    mock_client: MockDriveClient,
    mock_github_client: MockGitHubClient,
    settings: Settings,
) -> None:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    create_all(engine)
    repo = SqlMasterCvRepository(create_session_factory(engine))

    await refresh_master_cv(mock_client, mock_github_client, "u1", repo, settings)
    await refresh_master_cv(mock_client, mock_github_client, "u1", repo, settings)

    with engine.connect() as conn:
        sources = conn.execute(sa.text("SELECT COUNT(*) FROM cv_sources")).scalar_one()
        versions = conn.execute(sa.text("SELECT COUNT(*) FROM master_cv")).scalar_one()
    # 4 Drive artifacts + 2 GitHub repos = 6 deduped sources; one idempotent version.
    assert sources == 6
    assert versions == 1
