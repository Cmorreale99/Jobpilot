"""Selects the claim extractor from config.

Mirrors the other LLM factories (``matching_factory`` et al.): safe default first —
the deterministic :class:`HeuristicTwoPassExtractor` unless ``claims_llm_extraction``
is on. Same footgun guard: the flag without a real client (``llm_enabled=false`` and
no injected client) logs a warning and stays heuristic rather than handing the
extractor the offline fake. An explicitly injected ``llm_client`` (e.g. a scripted
fake in tests) is always honored.
"""

from __future__ import annotations

import logging

from app.config import Settings, get_settings
from app.domain.claims import ClaimExtractor, HeuristicTwoPassExtractor
from app.llm.client import LlmClient
from app.llm.extraction import LlmTwoPassExtractor
from app.llm.factory import create_llm_client

logger = logging.getLogger(__name__)


def create_claim_extractor(
    settings: Settings | None = None,
    *,
    llm_client: LlmClient | None = None,
) -> ClaimExtractor:
    """Return the LLM two-pass extractor when configured, else the heuristic."""
    settings = settings or get_settings()

    if not settings.claims_llm_extraction:
        return HeuristicTwoPassExtractor()

    if llm_client is None:
        if not settings.llm_enabled:
            logger.warning(
                "CLAIMS_LLM_EXTRACTION is on but LLM_ENABLED is off; "
                "falling back to the heuristic two-pass extractor."
            )
            return HeuristicTwoPassExtractor()
        llm_client = create_llm_client(settings)

    return LlmTwoPassExtractor(llm_client)
