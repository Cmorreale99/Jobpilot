"""End-to-end matching: build a Master CV from mocks, rank fixture jobs, persist."""

from __future__ import annotations

import sqlalchemy as sa
from app.config import Settings
from app.db.job_repository import SqlJobRepository
from app.db.session import create_all, create_session_factory
from app.integrations.mock.drive import MockDriveClient
from app.integrations.mock.github import MockGitHubClient
from app.integrations.mock.jobs import MockJobSource
from app.services.job_repository import InMemoryJobRepository
from app.services.master_cv_ingestion import build_master_cv
from app.services.matching import run_matching


async def test_pipeline_ranks_relevant_jobs_first(
    mock_client: MockDriveClient,
    mock_github_client: MockGitHubClient,
    mock_job_source: MockJobSource,
    settings: Settings,
) -> None:
    cv = await build_master_cv(mock_client, mock_github_client, "u1", settings)
    repo = InMemoryJobRepository()

    matches = await run_matching(mock_job_source, cv, "u1", repo, settings)

    assert 1 <= len(matches) <= settings.top_n
    ranks = [m.rank for m in matches]
    assert ranks == sorted(ranks)  # 1..n in order
    scores = [m.score for m in matches]
    assert scores == sorted(scores, reverse=True)  # non-increasing

    top_ids = {m.job.external_id for m in matches[:4]}
    # Payments / distributed / fraud roles should dominate the top; marketing/design should not.
    assert top_ids & {"job-1001", "job-1002", "job-1003", "job-1010", "job-1004"}
    assert "job-1007" not in {m.job.external_id for m in matches[:3]}  # marketing not top-3

    # Persisted and retrievable.
    stored = repo.get_matches("u1", cv.version)
    assert [m.job.external_id for m in stored] == [m.job.external_id for m in matches]


async def test_pipeline_is_idempotent_and_dedupes_jobs(
    mock_client: MockDriveClient,
    mock_github_client: MockGitHubClient,
    mock_job_source: MockJobSource,
    settings: Settings,
) -> None:
    cv = await build_master_cv(mock_client, mock_github_client, "u1", settings)
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    create_all(engine)
    repo = SqlJobRepository(create_session_factory(engine))

    first = await run_matching(mock_job_source, cv, "u1", repo, settings)
    second = await run_matching(mock_job_source, cv, "u1", repo, settings)

    assert [(m.job.external_id, m.rank) for m in first] == [
        (m.job.external_id, m.rank) for m in second
    ]
    with engine.connect() as conn:
        jobs = conn.execute(sa.text("SELECT COUNT(*) FROM jobs")).scalar_one()
        matches = conn.execute(sa.text("SELECT COUNT(*) FROM job_matches")).scalar_one()
    assert jobs == 10  # deduped across the two runs
    assert matches == len(first)  # replace semantics, not accumulation
