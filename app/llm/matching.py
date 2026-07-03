"""LLM-backed two-stage job matching.

Drop-in implementations of the domain :class:`~app.domain.matching.JobScorer` (stage-1,
BULK model) and :class:`~app.domain.matching.JobReranker` (stage-2, DEEP model) protocols,
so ``run_two_stage`` works unchanged with either these or the heuristics. Living in
``app/llm/`` keeps ``domain/`` free of any LLM import.

Resilience matters here: matching runs nightly over many jobs, so a single flaky call must
not sink the batch. The scorer falls back to a zero score for a failed job; the reranker
falls back to the deterministic heuristic ordering for the whole shortlist. Both degrade to
"this job/shortlist ranks conservatively", never to a crash.
"""

from __future__ import annotations

import logging
from typing import Any

from app.domain.jobs import Job, JobMatch, ScoredJob
from app.domain.matching import CvProfile, HeuristicJobReranker
from app.llm.client import LlmClient
from app.llm.errors import LlmError
from app.llm.json_completion import complete_json
from app.llm.types import LlmMessage, ModelTier

logger = logging.getLogger("app.llm.matching")

# Keep job text bounded so stage-1 prompts stay cheap and stage-2 fits many jobs at once.
_MAX_DESC_CHARS = 1500
_MAX_RERANK_DESC_CHARS = 600

_SCORER_SYSTEM = (
    "You assess how well a single job posting fits a candidate, based ONLY on the "
    "candidate's career evidence and the posting text provided. Judge on skills, domain, "
    "seniority, and responsibilities — not location or perks. Be calibrated: 1.0 is an "
    "exceptional fit, 0.5 a plausible stretch, 0.0 unrelated.\n"
    'Reply with ONLY JSON of the form {"score": <number 0..1>, '
    '"matched_terms": [<up to 8 short skill/domain phrases present in BOTH the evidence '
    "and the posting>]}. No prose, no code fences."
)

_RERANKER_SYSTEM = (
    "You are the deep-ranking stage of a job-matching pipeline. Given a candidate's career "
    "evidence and a shortlist of postings, select and order the best fits. Judge on skills, "
    "domain, seniority, and responsibilities. Ground every rationale in the candidate's "
    "actual evidence and the posting — never invent experience the candidate does not have.\n"
    'Reply with ONLY JSON of the form {"ranking": [{"id": "<job id>", '
    '"score": <number 0..1>, "rationale": "<one or two sentences>"}]}. Order best first, '
    "include at most the requested number, omit clearly irrelevant postings, and use only "
    "ids from the shortlist. No prose, no code fences."
)


class LlmJobScorer:
    """Stage-1 bulk scorer: one cheap LLM call per job, resilient to per-job failure."""

    def __init__(
        self,
        client: LlmClient,
        *,
        tier: ModelTier = ModelTier.BULK,
        max_tokens: int = 512,
    ) -> None:
        self._client = client
        self._tier = tier
        self._max_tokens = max_tokens

    def score(self, profile: CvProfile, job: Job) -> ScoredJob:
        if not profile.summary.strip():
            return ScoredJob(job=job, score=0.0)
        try:
            result = complete_json(
                self._client,
                system=_SCORER_SYSTEM,
                # The evidence block is identical for every job scored in a run; as
                # cached context the first call writes the prompt cache and the rest
                # of the batch reads it at ~0.1x input price.
                cached_context=_evidence_block(profile),
                messages=[LlmMessage.user(_scorer_prompt(job))],
                tier=self._tier,
                validator=_parse_score,
                max_tokens=self._max_tokens,
            )
        except LlmError as exc:
            logger.warning("stage-1 scoring failed for job %s: %s", job.external_id, exc)
            return ScoredJob(job=job, score=0.0)
        score, matched = result
        return ScoredJob(job=job, score=score, matched_terms=matched)


class LlmJobReranker:
    """Stage-2 deep reranker: one DEEP call over the shortlist; heuristic fallback."""

    def __init__(
        self,
        client: LlmClient,
        *,
        tier: ModelTier = ModelTier.DEEP,
        max_tokens: int = 2048,
    ) -> None:
        self._client = client
        self._tier = tier
        self._max_tokens = max_tokens
        self._fallback = HeuristicJobReranker()

    def rerank(self, profile: CvProfile, shortlist: list[ScoredJob], top_n: int) -> list[JobMatch]:
        if not shortlist or top_n <= 0:
            return []
        if not profile.summary.strip():
            return self._fallback.rerank(profile, shortlist, top_n)

        # Address jobs by a stable positional id so duplicate external_ids can't collide.
        by_id = {f"j{i}": scored for i, scored in enumerate(shortlist)}
        # No cached_context here: this is one call per run, so a cache write (1.25x)
        # would never be read back — and DEEP is a different model than the scorer's
        # BULK cache anyway (caches are per-model).
        try:
            ranking = complete_json(
                self._client,
                system=_RERANKER_SYSTEM,
                messages=[LlmMessage.user(_reranker_prompt(profile, by_id, top_n))],
                tier=self._tier,
                validator=_parse_ranking,
                max_tokens=self._max_tokens,
            )
        except LlmError as exc:
            logger.warning("stage-2 rerank failed; falling back to heuristic order: %s", exc)
            return self._fallback.rerank(profile, shortlist, top_n)

        matches: list[JobMatch] = []
        seen: set[str] = set()
        for entry_id, score, rationale in ranking:
            scored = by_id.get(entry_id)
            if scored is None or entry_id in seen:
                continue  # ignore hallucinated or duplicate ids
            seen.add(entry_id)
            matches.append(
                JobMatch(
                    job=scored.job,
                    score=score,
                    rank=len(matches) + 1,  # re-rank by output order, ignore model's rank
                    rationale=rationale,
                    stage="deep",
                    matched_terms=scored.matched_terms,
                )
            )
            if len(matches) >= top_n:
                break
        # If the model returned nothing usable, don't silently drop the shortlist.
        return matches or self._fallback.rerank(profile, shortlist, top_n)


# --- prompts ---------------------------------------------------------------------


def _evidence_block(profile: CvProfile) -> str:
    return f"Candidate career evidence (PAR claims):\n{profile.summary}"


def _scorer_prompt(job: Job) -> str:
    return (
        "Job posting:\n"
        f"Title: {job.title}\n"
        f"Company: {job.company}\n"
        f"Description: {_truncate(job.description, _MAX_DESC_CHARS)}\n\n"
        "Score the fit."
    )


def _reranker_prompt(profile: CvProfile, by_id: dict[str, ScoredJob], top_n: int) -> str:
    lines = [
        "Candidate career evidence (PAR claims):",
        profile.summary,
        "",
        f"Shortlist ({len(by_id)} postings). Select and order the top {top_n}:",
    ]
    for entry_id, scored in by_id.items():
        job = scored.job
        lines.append(
            f"- id={entry_id} | {job.title} @ {job.company} | "
            f"{_truncate(job.description, _MAX_RERANK_DESC_CHARS)}"
        )
    return "\n".join(lines)


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


# --- response parsing ------------------------------------------------------------


def _parse_score(payload: Any) -> tuple[float, tuple[str, ...]]:
    if not isinstance(payload, dict) or "score" not in payload:
        raise ValueError("expected an object with a 'score'")
    score = _clamp01(payload["score"])
    matched = _string_list(payload.get("matched_terms"))[:8]
    return score, tuple(matched)


def _parse_ranking(payload: Any) -> list[tuple[str, float, str]]:
    if not isinstance(payload, dict) or "ranking" not in payload:
        raise ValueError("expected an object with a 'ranking' array")
    raw = payload["ranking"]
    if not isinstance(raw, list):
        raise ValueError("'ranking' must be an array")
    result: list[tuple[str, float, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("each ranking entry must be an object")
        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or not entry_id:
            raise ValueError("each ranking entry needs a string 'id'")
        rationale = entry.get("rationale")
        result.append(
            (
                entry_id,
                _clamp01(entry.get("score")),
                rationale.strip() if isinstance(rationale, str) and rationale.strip() else "",
            )
        )
    return result


def _clamp01(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"expected a number, got {value!r}")
    return max(0.0, min(1.0, round(float(value), 4)))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]
