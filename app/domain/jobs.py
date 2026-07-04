"""Job postings, match results, and the persistence contract.

Pure domain types. A :class:`Job` is a normalized posting (whatever the source, it lands
here); a :class:`JobMatch` is the result of ranking a job against a Master CV version.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol, runtime_checkable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Query parameters that carry tracking state, not identity. Everything else is kept —
# many ATSes key the posting on a query parameter.
_TRACKING_PARAMS_PREFIXES = ("utm_",)
_TRACKING_PARAMS = frozenset({"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "src", "source"})


def canonical_job_url(url: str | None) -> str | None:
    """The canonical form of a posting URL, when resolvable — else ``None``.

    Deterministic normalization only (never invented): lowercase scheme/host, drop
    the fragment and known tracking parameters. A relative or unparseable value is
    not resolvable and yields ``None``; the original URL is always stored alongside.
    """
    if not url or not url.strip():
        return None
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_PARAMS
        and not key.lower().startswith(_TRACKING_PARAMS_PREFIXES)
    ]
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path,
            urlencode(query),
            "",  # fragment dropped
        )
    )


@dataclass(frozen=True)
class Job:
    """A normalized job posting. Deduped on ``(source, external_id)``.

    ``url`` is the posting exactly as the source gave it; ``canonical_url`` is the
    tracking-stripped canonical form when resolvable (stamped at ingestion).
    """

    source: str
    external_id: str
    title: str
    company: str
    description: str
    location: str | None = None
    url: str | None = None
    canonical_url: str | None = None
    posted_at: datetime | None = None
    remote: bool = False

    @property
    def ref(self) -> tuple[str, str]:
        return (self.source, self.external_id)

    def with_canonical_url(self) -> Job:
        """Stamp the canonical URL from ``url`` (keeps an explicit one if already set)."""
        if self.canonical_url is not None:
            return self
        canonical = canonical_job_url(self.url)
        return replace(self, canonical_url=canonical) if canonical else self


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

    def get_job(self, source: str, external_id: str) -> Job | None:
        """Look up one stored job (for linking cards back to the posting)."""
        ...

    def save_matches(self, user_id: str, master_cv_version: int, matches: list[JobMatch]) -> None:
        """Replace the stored matches for ``(user_id, master_cv_version)`` (idempotent)."""
        ...

    def get_matches(self, user_id: str, master_cv_version: int) -> list[JobMatch]:
        """Return stored matches for a user's CV version, ordered by rank."""
        ...
