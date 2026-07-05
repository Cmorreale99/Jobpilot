"""End-to-end matching: rank fixture jobs against an approved-claims Master CV, persist."""

from __future__ import annotations

import sqlalchemy as sa
from app.config import Settings
from app.db.job_repository import SqlJobRepository
from app.db.session import create_all, create_session_factory
from app.domain.cv import MasterCv, ParClaim
from app.integrations.mock.jobs import MockJobSource
from app.services.job_repository import InMemoryJobRepository
from app.services.matching import run_matching


def _cv() -> MasterCv:
    """A Master CV shaped like the adapter's output: approved claims, claim:<id> refs."""

    def claim(claim_id: int, action: str, result: str | None = None) -> ParClaim:
        return ParClaim(
            action=action,
            result=result,
            source_type="approved_claim",
            source_ref=f"claim:{claim_id}",
        )

    return MasterCv(
        claims=[
            claim(
                1,
                "Re-architected the settlement pipeline over Kafka in Python for payments",
                "Cut settlement batch runtime by 70%",
            ),
            claim(2, "Built streaming payment reconciliation and ledger services with SQL"),
            claim(
                3,
                "Deployed distributed systems with Kubernetes and Terraform across regions",
                "Reduced deploy latency by half",
            ),
            claim(4, "Developed fraud detection models and anomaly scoring in Python"),
            claim(5, "Instrumented observability for high-throughput backend APIs with Grafana"),
        ],
        version=1,
    )


async def test_pipeline_ranks_relevant_jobs_first(
    mock_job_source: MockJobSource,
    settings: Settings,
) -> None:
    cv = _cv()
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
    mock_job_source: MockJobSource,
    settings: Settings,
) -> None:
    cv = _cv()
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
