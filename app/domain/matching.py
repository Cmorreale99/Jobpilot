"""Two-stage job matching against a Master CV — pure, deterministic, offline.

Stage 1 (bulk): score every fetched job cheaply and shortlist the strongest.
Stage 2 (deep): re-rank the shortlist into the final top-N, each with a rationale.

Both stages sit behind protocols (:class:`JobScorer`, :class:`JobReranker`) so the
heuristic implementations here can be swapped for LLM-backed ones (`app/llm/`) without
changing the orchestration. The default heuristics use keyword overlap between the CV's
PAR claims and the job text — explainable and reproducible, which is what a matching
pipeline needs to be testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from app.domain.cv import MasterCv
from app.domain.jobs import Job, JobMatch, ScoredJob

# Small stopword set — enough to keep keyword overlap meaningful without a dependency.
_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "you",
        "our",
        "are",
        "will",
        "who",
        "your",
        "this",
        "that",
        "have",
        "from",
        "was",
        "were",
        "has",
        "had",
        "not",
        "but",
        "all",
        "any",
        "can",
        "job",
        "role",
        "team",
        "work",
        "working",
        "years",
        "year",
        "experience",
        "including",
        "such",
        "into",
        "per",
        "via",
        "using",
        "use",
        "used",
        "able",
        "well",
        "new",
        "high",
        "across",
        "within",
        "their",
        "them",
        "they",
        "its",
        "a",
        "an",
        "to",
        "of",
        "in",
        "on",
        "at",
        "as",
        "is",
        "be",
        "or",
        "we",
        "us",
        "by",
    }
)
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#.\-]{1,}")


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if len(t) >= 3 and t not in _STOPWORDS]


@dataclass(frozen=True)
class CvProfile:
    """A CV's matching profile, built once and reused across all jobs.

    ``tokens`` powers the keyword-overlap heuristic; ``summary`` is a compact PAR rendering
    of the same claims so an LLM-backed scorer/reranker can reason about fit. Enriching the
    profile (rather than passing the whole ``MasterCv``) keeps the scorer/reranker protocols
    unchanged across both implementations.
    """

    tokens: frozenset[str]
    summary: str = ""


def build_profile(master_cv: MasterCv) -> CvProfile:
    """Extract the CV's matching profile from its PAR claims (works on reconstructed CVs)."""
    parts: list[str] = []
    lines: list[str] = []
    for claim in master_cv.claims:
        parts.extend([claim.action, claim.problem or "", claim.result or "", claim.evidence_text])
        segments = []
        if claim.problem:
            segments.append(f"Problem: {claim.problem}")
        segments.append(f"Action: {claim.action}")
        if claim.result:
            segments.append(f"Result: {claim.result}")
        lines.append(" | ".join(segments))
    return CvProfile(tokens=frozenset(_tokenize(" ".join(parts))), summary="\n".join(lines))


class JobScorer(Protocol):
    """Stage-1 bulk scorer: cheap relevance score in [0, 1] for one job."""

    def score(self, profile: CvProfile, job: Job) -> ScoredJob: ...


class JobReranker(Protocol):
    """Stage-2 deep reranker: order a shortlist into final matches with rationale."""

    def rerank(
        self, profile: CvProfile, shortlist: list[ScoredJob], top_n: int
    ) -> list[JobMatch]: ...


class HeuristicJobScorer:
    """Keyword-overlap scorer, with a small bonus for title matches."""

    def score(self, profile: CvProfile, job: Job) -> ScoredJob:
        job_tokens = set(_tokenize(f"{job.title} {job.description}"))
        if not job_tokens:
            return ScoredJob(job=job, score=0.0)
        overlap = profile.tokens & job_tokens
        title_overlap = profile.tokens & set(_tokenize(job.title))
        base = len(overlap) / len(job_tokens)
        score = min(1.0, round(base + 0.1 * len(title_overlap), 4))
        return ScoredJob(job=job, score=score, matched_terms=tuple(sorted(overlap)))


class HeuristicJobReranker:
    """Orders the shortlist by score (stable) and attaches a rationale."""

    def rerank(self, profile: CvProfile, shortlist: list[ScoredJob], top_n: int) -> list[JobMatch]:
        ordered = sorted(shortlist, key=lambda s: (-s.score, s.job.external_id))[:top_n]
        matches: list[JobMatch] = []
        for rank, scored in enumerate(ordered, start=1):
            top_terms = scored.matched_terms[:8]
            rationale = (
                "Strong overlap on: " + ", ".join(top_terms)
                if top_terms
                else "No strong keyword overlap with the Master CV."
            )
            matches.append(
                JobMatch(
                    job=scored.job,
                    score=scored.score,
                    rank=rank,
                    rationale=rationale,
                    stage="deep",
                    matched_terms=scored.matched_terms,
                )
            )
        return matches


def score_all(profile: CvProfile, jobs: list[Job], scorer: JobScorer) -> list[ScoredJob]:
    """Stage 1: score every job."""
    return [scorer.score(profile, job) for job in jobs]


def shortlist(scored: list[ScoredJob], size: int) -> list[ScoredJob]:
    """Stage 1: keep the top ``size`` by score (stable tie-break on external_id)."""
    return sorted(scored, key=lambda s: (-s.score, s.job.external_id))[:size]


def run_two_stage(
    master_cv: MasterCv,
    jobs: list[Job],
    scorer: JobScorer,
    reranker: JobReranker,
    *,
    shortlist_size: int,
    top_n: int,
) -> list[JobMatch]:
    """Full pipeline: score all → shortlist → deep re-rank into the final top-N."""
    profile = build_profile(master_cv)
    scored = score_all(profile, jobs, scorer)
    top = shortlist(scored, shortlist_size)
    return reranker.rerank(profile, top, top_n)
