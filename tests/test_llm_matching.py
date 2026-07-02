"""Offline tests for the LLM-backed two-stage matchers.

Driven by scripted ``FakeLlmClient`` responses. Focus: tier selection, score/rationale
mapping, id-grounding (hallucinated ids dropped), and graceful degradation on LLM failure.
"""

from __future__ import annotations

import json

from app.domain.cv import MasterCv, ParClaim
from app.domain.jobs import Job, ScoredJob
from app.domain.matching import build_profile
from app.llm import FakeLlmClient, LlmJobReranker, LlmJobScorer, ModelTier
from app.llm.matching import _truncate


def _profile() -> object:
    cv = MasterCv(
        claims=[
            ParClaim(
                action="Built idempotent Kafka settlement workers in Go",
                source_ref="resume",
                problem="Missed banking cutoffs",
                result="Eliminated missed cutoffs",
            )
        ]
    )
    return build_profile(cv)


def _job(external_id: str, title: str = "Backend Engineer", desc: str = "Kafka, Go") -> Job:
    return Job(
        source="mock",
        external_id=external_id,
        title=title,
        company="ACME",
        description=desc,
    )


# --- scorer ----------------------------------------------------------------------


def test_scorer_maps_score_and_terms_and_uses_bulk_tier() -> None:
    client = FakeLlmClient([json.dumps({"score": 0.82, "matched_terms": ["kafka", "go"]})])
    scored = LlmJobScorer(client).score(_profile(), _job("j1"))  # type: ignore[arg-type]
    assert scored.score == 0.82
    assert scored.matched_terms == ("kafka", "go")
    assert client.calls[0].tier is ModelTier.BULK


def test_scorer_clamps_out_of_range_score() -> None:
    client = FakeLlmClient([json.dumps({"score": 1.9})])
    scored = LlmJobScorer(client).score(_profile(), _job("j1"))  # type: ignore[arg-type]
    assert scored.score == 1.0


def test_scorer_empty_cv_makes_no_call() -> None:
    client = FakeLlmClient([json.dumps({"score": 1.0})])
    scored = LlmJobScorer(client).score(build_profile(MasterCv()), _job("j1"))
    assert scored.score == 0.0
    assert client.call_count == 0


def test_scorer_degrades_to_zero_on_llm_failure() -> None:
    client = FakeLlmClient(["not json", "still not json"])  # exhausts the retry budget
    scored = LlmJobScorer(client).score(_profile(), _job("j1"))  # type: ignore[arg-type]
    assert scored.score == 0.0
    assert scored.matched_terms == ()


# --- reranker --------------------------------------------------------------------


def _shortlist() -> list[ScoredJob]:
    return [
        ScoredJob(job=_job("j-pay", "Payments Engineer"), score=0.7, matched_terms=("kafka",)),
        ScoredJob(job=_job("j-mkt", "Marketing Manager"), score=0.2),
        ScoredJob(job=_job("j-sre", "SRE"), score=0.5),
    ]


def test_reranker_orders_by_model_output_and_uses_deep_tier() -> None:
    ranking = {
        "ranking": [
            {"id": "j2", "score": 0.6, "rationale": "Strong ops overlap."},
            {"id": "j0", "score": 0.9, "rationale": "Direct payments + Kafka fit."},
        ]
    }
    client = FakeLlmClient([json.dumps(ranking)])
    matches = LlmJobReranker(client).rerank(_profile(), _shortlist(), top_n=2)  # type: ignore[arg-type]

    assert client.calls[0].tier is ModelTier.DEEP
    # Rank follows model output order, not the model's claimed score ordering.
    assert [m.job.external_id for m in matches] == ["j-sre", "j-pay"]
    assert [m.rank for m in matches] == [1, 2]
    assert matches[1].rationale == "Direct payments + Kafka fit."
    # Stage-1 matched terms are carried through onto the deep match.
    assert matches[1].matched_terms == ("kafka",)


def test_reranker_ignores_hallucinated_and_duplicate_ids() -> None:
    ranking = {
        "ranking": [
            {"id": "j99", "score": 0.9, "rationale": "does not exist"},
            {"id": "j0", "score": 0.8, "rationale": "real"},
            {"id": "j0", "score": 0.7, "rationale": "dup"},
        ]
    }
    client = FakeLlmClient([json.dumps(ranking)])
    matches = LlmJobReranker(client).rerank(_profile(), _shortlist(), top_n=5)  # type: ignore[arg-type]
    assert [m.job.external_id for m in matches] == ["j-pay"]


def test_reranker_respects_top_n() -> None:
    ranking = {"ranking": [{"id": f"j{i}", "score": 0.5, "rationale": "x"} for i in range(3)]}
    client = FakeLlmClient([json.dumps(ranking)])
    matches = LlmJobReranker(client).rerank(_profile(), _shortlist(), top_n=1)  # type: ignore[arg-type]
    assert len(matches) == 1


def test_reranker_falls_back_to_heuristic_on_failure() -> None:
    client = FakeLlmClient(["nope", "nope"])  # exhausts retries -> LlmJsonError
    matches = LlmJobReranker(client).rerank(_profile(), _shortlist(), top_n=2)  # type: ignore[arg-type]
    # Heuristic fallback orders by stage-1 score: j-pay (0.7) then j-sre (0.5).
    assert [m.job.external_id for m in matches] == ["j-pay", "j-sre"]
    assert [m.rank for m in matches] == [1, 2]


def test_reranker_falls_back_when_model_returns_no_usable_ids() -> None:
    ranking = {"ranking": [{"id": "j99", "score": 1.0, "rationale": ""}]}
    client = FakeLlmClient([json.dumps(ranking)])
    matches = LlmJobReranker(client).rerank(_profile(), _shortlist(), top_n=2)  # type: ignore[arg-type]
    assert [m.job.external_id for m in matches] == ["j-pay", "j-sre"]


def test_reranker_empty_shortlist_returns_empty() -> None:
    client = FakeLlmClient([json.dumps({"ranking": []})])
    assert LlmJobReranker(client).rerank(_profile(), [], top_n=3) == []  # type: ignore[arg-type]
    assert client.call_count == 0


# --- helpers ---------------------------------------------------------------------


def test_truncate_collapses_whitespace_and_caps_length() -> None:
    assert _truncate("a   b\n\nc", 100) == "a b c"
    long = "word " * 100
    assert len(_truncate(long, 20)) <= 21  # 20 chars + ellipsis
