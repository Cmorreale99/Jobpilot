"""Job matching orchestration: fetch → persist jobs → two-stage rank → persist matches.

Depends only on the :class:`JobSource` and :class:`JobRepository` interfaces and the pure
matching domain, so it runs identically against mocks or a real compliant job source.
"""

from __future__ import annotations

from datetime import datetime

from app.config import Settings, get_settings
from app.domain.cv import MasterCv
from app.domain.jobs import JobMatch, JobRepository
from app.domain.matching import (
    HeuristicJobReranker,
    HeuristicJobScorer,
    JobReranker,
    JobScorer,
    run_two_stage,
)
from app.integrations.base import JobSource


async def run_matching(
    job_source: JobSource,
    master_cv: MasterCv,
    user_id: str,
    repository: JobRepository,
    settings: Settings | None = None,
    *,
    scorer: JobScorer | None = None,
    reranker: JobReranker | None = None,
    since: datetime | None = None,
) -> list[JobMatch]:
    """Fetch recent jobs, rank them against ``master_cv``, and persist the top matches."""
    settings = settings or get_settings()
    jobs = await job_source.fetch_recent_jobs(since)
    repository.upsert_jobs(jobs)

    matches = run_two_stage(
        master_cv,
        jobs,
        scorer or HeuristicJobScorer(),
        reranker or HeuristicJobReranker(),
        shortlist_size=settings.shortlist_size,
        top_n=settings.top_n,
    )
    repository.save_matches(user_id, master_cv.version, matches)
    return matches
