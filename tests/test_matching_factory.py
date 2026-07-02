"""Tests for the config-driven matcher factory and its run_matching wiring.

Offline throughout: the LLM path uses a scripted ``FakeLlmClient``, never a real client.
"""

from __future__ import annotations

import json
import logging

import pytest
from app.config import Settings
from app.domain.cv import MasterCv, ParClaim
from app.domain.matching import HeuristicJobReranker, HeuristicJobScorer
from app.integrations.mock.jobs import MockJobSource
from app.llm import FakeLlmClient, LlmJobReranker, LlmJobScorer
from app.services import matching_factory
from app.services.job_repository import InMemoryJobRepository
from app.services.matching import run_matching
from app.services.matching_factory import create_matchers


def _cv() -> MasterCv:
    return MasterCv(
        claims=[
            ParClaim(action="Built Kafka payment settlement systems", source_ref="resume"),
        ]
    )


# --- factory branches ------------------------------------------------------------


def test_default_uses_heuristic_matchers() -> None:
    scorer, reranker = create_matchers(Settings())
    assert isinstance(scorer, HeuristicJobScorer)
    assert isinstance(reranker, HeuristicJobReranker)


def test_flag_on_with_injected_client_uses_llm() -> None:
    fake = FakeLlmClient()
    scorer, reranker = create_matchers(Settings(matching_llm_ranking=True), llm_client=fake)
    assert isinstance(scorer, LlmJobScorer)
    assert isinstance(reranker, LlmJobReranker)


def test_flag_on_without_real_client_falls_back(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=matching_factory.__name__):
        scorer, reranker = create_matchers(Settings(matching_llm_ranking=True, llm_enabled=False))
    assert isinstance(scorer, HeuristicJobScorer)
    assert isinstance(reranker, HeuristicJobReranker)
    assert "falling back to the heuristic" in caplog.text.lower()


def test_flag_on_with_real_client_builds_llm_matchers() -> None:
    scorer, reranker = create_matchers(
        Settings(matching_llm_ranking=True, llm_enabled=True, anthropic_api_key="sk-test")
    )
    assert isinstance(scorer, LlmJobScorer)
    assert isinstance(reranker, LlmJobReranker)


# --- run_matching wiring ---------------------------------------------------------


async def test_run_matching_routes_through_llm_when_flag_on(
    mock_job_source: MockJobSource,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the flag on and no injected matchers, run_matching uses the LLM matchers."""

    # Scorer returns a per-job score; reranker returns a ranking over positional ids.
    def handler(messages: object, tier: object) -> str:
        text = messages[-1].content  # type: ignore[index]
        if "Shortlist" in text:  # the reranker prompt
            return json.dumps({"ranking": [{"id": "j0", "score": 0.9, "rationale": "top pick"}]})
        return json.dumps({"score": 0.6, "matched_terms": ["kafka"]})

    fake = FakeLlmClient(handler=handler)
    monkeypatch.setattr(matching_factory, "create_llm_client", lambda *a, **k: fake)

    llm_settings = settings.model_copy(
        update={
            "matching_llm_ranking": True,
            "llm_enabled": True,
            "anthropic_api_key": "sk-test",
        }
    )
    repo = InMemoryJobRepository()
    matches = await run_matching(mock_job_source, _cv(), "u1", repo, llm_settings)

    assert matches, "expected the LLM matchers to produce matches"
    assert matches[0].rationale == "top pick"
    # One BULK scoring call per fetched job, plus one DEEP rerank call.
    assert fake.call_count >= 2


async def test_run_matching_uses_heuristic_when_flag_off(
    mock_job_source: MockJobSource, settings: Settings
) -> None:
    repo = InMemoryJobRepository()
    matches = await run_matching(mock_job_source, _cv(), "u1", repo, settings)
    assert matches  # heuristic path still ranks fixture jobs
