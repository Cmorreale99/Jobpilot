"""Job postings, match results, and the persistence contract.

Pure domain types. A :class:`Job` is a normalized posting (whatever the source, it lands
here); a :class:`JobMatch` is the result of ranking a job against a Master CV version.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Job:
    """A normalized job posting. Deduped on ``(source, external_id)``."""

    source: str
    external_id: str
    title: str
    company: str
    description: str
    location: str | None = None
    url: str | None = None
    posted_at: datetime | None = None
    remote: bool = False

    @property
    def ref(self) -> tuple[str, str]:
        return (self.source, self.external_id)


@dataclass(frozen=True)
class ScoredJob:
    """A job with its stage-1 bulk score and the CV terms it matched."""

    job: Job
    score: float
    matched_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class JobMatch:
    """A deep-ranked match: final score, rank, and a human-readable rationale."""

    job: Job
    score: float
    rank: int
    rationale: str
    stage: str = "deep"
    matched_terms: tuple[str, ...] = ()


@runtime_checkable
class JobRepository(Protocol):
    """Persistence for jobs (deduped) and per-CV-version match results."""

    def upsert_jobs(self, jobs: list[Job]) -> int:
        """Insert or update jobs by ``(source, external_id)``; return the count seen."""
        ...

    def save_matches(self, user_id: str, master_cv_version: int, matches: list[JobMatch]) -> None:
        """Replace the stored matches for ``(user_id, master_cv_version)`` (idempotent)."""
        ...

    def get_matches(self, user_id: str, master_cv_version: int) -> list[JobMatch]:
        """Return stored matches for a user's CV version, ordered by rank."""
        ...
