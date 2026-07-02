"""In-memory :class:`JobRepository` for mock-first tests and offline runs."""

from __future__ import annotations

from app.domain.jobs import Job, JobMatch


class InMemoryJobRepository:
    """Non-persistent job + match store with the same semantics as the SQL one."""

    def __init__(self) -> None:
        self._jobs: dict[tuple[str, str], Job] = {}
        self._matches: dict[tuple[str, int], list[JobMatch]] = {}

    def upsert_jobs(self, jobs: list[Job]) -> int:
        for job in jobs:
            self._jobs[job.ref] = job
        return len(jobs)

    def save_matches(self, user_id: str, master_cv_version: int, matches: list[JobMatch]) -> None:
        self._matches[(user_id, master_cv_version)] = list(matches)

    def get_matches(self, user_id: str, master_cv_version: int) -> list[JobMatch]:
        stored = self._matches.get((user_id, master_cv_version), [])
        return sorted(stored, key=lambda m: m.rank)

    @property
    def job_count(self) -> int:
        return len(self._jobs)
