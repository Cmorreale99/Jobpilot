"""Job repository: dedupe, match persistence, idempotent replace (in-memory + SQL)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from app.db.job_repository import SqlJobRepository
from app.db.session import create_all, create_session_factory
from app.domain.jobs import Job, JobMatch, JobRepository


def _job(external_id: str, title: str = "Engineer") -> Job:
    return Job(source="mock", external_id=external_id, title=title, company="ACME", description="x")


def _match(job: Job, rank: int, score: float) -> JobMatch:
    return JobMatch(
        job=job, score=score, rank=rank, rationale="because", matched_terms=("go", "kafka")
    )


@pytest.fixture(params=["memory", "sql"])
def repo(request: pytest.FixtureRequest) -> Iterator[JobRepository]:
    if request.param == "memory":
        from app.services.job_repository import InMemoryJobRepository

        yield InMemoryJobRepository()
    else:
        engine = sa.create_engine("sqlite+pysqlite:///:memory:")
        create_all(engine)
        yield SqlJobRepository(create_session_factory(engine))


def test_upsert_jobs_dedupes(repo: JobRepository) -> None:
    repo.upsert_jobs([_job("j1"), _job("j2")])
    repo.upsert_jobs([_job("j1", title="Senior Engineer")])  # same ref -> update, not add
    # Re-fetch via matches path is indirect; assert via a fresh matcher save/read instead.
    repo.save_matches("u1", 1, [_match(_job("j1", "Senior Engineer"), 1, 0.9)])
    matches = repo.get_matches("u1", 1)
    assert len(matches) == 1
    assert matches[0].job.title == "Senior Engineer"


def test_save_and_get_matches_round_trip(repo: JobRepository) -> None:
    j1, j2 = _job("j1"), _job("j2")
    repo.upsert_jobs([j1, j2])
    repo.save_matches("u1", 1, [_match(j1, 1, 0.9), _match(j2, 2, 0.5)])
    matches = repo.get_matches("u1", 1)
    assert [m.rank for m in matches] == [1, 2]
    assert matches[0].job.external_id == "j1"
    assert matches[0].matched_terms == ("go", "kafka")
    assert matches[0].score == 0.9


def test_save_matches_replaces_previous(repo: JobRepository) -> None:
    j1, j2 = _job("j1"), _job("j2")
    repo.upsert_jobs([j1, j2])
    repo.save_matches("u1", 1, [_match(j1, 1, 0.9), _match(j2, 2, 0.5)])
    repo.save_matches("u1", 1, [_match(j2, 1, 0.7)])  # idempotent replace
    matches = repo.get_matches("u1", 1)
    assert len(matches) == 1
    assert matches[0].job.external_id == "j2"


def test_matches_isolated_by_cv_version(repo: JobRepository) -> None:
    j1 = _job("j1")
    repo.upsert_jobs([j1])
    repo.save_matches("u1", 1, [_match(j1, 1, 0.9)])
    repo.save_matches("u1", 2, [_match(j1, 1, 0.4)])
    assert repo.get_matches("u1", 1)[0].score == 0.9
    assert repo.get_matches("u1", 2)[0].score == 0.4


def test_sql_upsert_does_not_duplicate_rows() -> None:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    create_all(engine)
    repo = SqlJobRepository(create_session_factory(engine))
    repo.upsert_jobs([_job("j1"), _job("j2")])
    repo.upsert_jobs([_job("j1"), _job("j2")])  # same refs again
    with engine.connect() as conn:
        count = conn.execute(sa.text("SELECT COUNT(*) FROM jobs")).scalar_one()
    assert count == 2
