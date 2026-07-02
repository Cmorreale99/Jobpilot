"""Selects the prep-packet generator from config.

Service-layer factory (may import ``app/llm/``; ``domain/`` may not). Same footgun
guard as the other LLM factories: heuristic by default, LLM-backed when
``interview_llm_prep`` is on and a real client is available; otherwise it logs and
falls back. An explicitly injected ``llm_client`` is always honored.
"""

from __future__ import annotations

import logging

from app.config import Settings, get_settings
from app.domain.interviews import HeuristicPrepPacketGenerator, PrepPacketGenerator
from app.llm.client import LlmClient
from app.llm.factory import create_llm_client
from app.llm.prep import LlmPrepPacketGenerator

logger = logging.getLogger(__name__)


def create_prep_generator(
    settings: Settings | None = None,
    *,
    llm_client: LlmClient | None = None,
) -> PrepPacketGenerator:
    """Return the prep generator — LLM-backed when configured, else heuristic."""
    settings = settings or get_settings()

    if not settings.interview_llm_prep:
        return HeuristicPrepPacketGenerator()

    if llm_client is None:
        if not settings.llm_enabled:
            logger.warning(
                "INTERVIEW_LLM_PREP is on but LLM_ENABLED is off; "
                "falling back to the heuristic prep generator."
            )
            return HeuristicPrepPacketGenerator()
        llm_client = create_llm_client(settings)

    return LlmPrepPacketGenerator(llm_client)
