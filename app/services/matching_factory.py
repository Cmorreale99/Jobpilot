"""Selects the two-stage matchers (scorer + reranker) from config.

Service-layer factory, so it may import ``app/llm/`` (``domain/`` may not). Same shape and
footgun guard as :mod:`app.services.cv_builder_factory`: heuristic by default, LLM-backed
when ``matching_llm_ranking`` is on and a real client is available; otherwise it logs and
falls back to the heuristic rather than handing the matchers the offline fake (which returns
non-JSON and fails). An explicitly injected ``llm_client`` is always honored.
"""

from __future__ import annotations

import logging

from app.config import Settings, get_settings
from app.domain.matching import (
    HeuristicJobReranker,
    HeuristicJobScorer,
    JobReranker,
    JobScorer,
)
from app.llm.client import LlmClient
from app.llm.factory import create_llm_client
from app.llm.matching import LlmJobReranker, LlmJobScorer

logger = logging.getLogger(__name__)


def create_matchers(
    settings: Settings | None = None,
    *,
    llm_client: LlmClient | None = None,
) -> tuple[JobScorer, JobReranker]:
    """Return ``(scorer, reranker)`` — LLM-backed when configured, else heuristic."""
    settings = settings or get_settings()

    if not settings.matching_llm_ranking:
        return HeuristicJobScorer(), HeuristicJobReranker()

    if llm_client is None:
        if not settings.llm_enabled:
            logger.warning(
                "MATCHING_LLM_RANKING is on but LLM_ENABLED is off; "
                "falling back to the heuristic matchers."
            )
            return HeuristicJobScorer(), HeuristicJobReranker()
        llm_client = create_llm_client(settings)

    return LlmJobScorer(llm_client), LlmJobReranker(llm_client)
