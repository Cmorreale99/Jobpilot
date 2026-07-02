"""Selects the tailorer + outreach drafter from config.

Service-layer factory, so it may import ``app/llm/`` (``domain/`` may not). Same shape
and footgun guard as :mod:`app.services.matching_factory`: heuristic by default,
LLM-backed when ``tailoring_llm_drafting`` is on and a real client is available;
otherwise it logs and falls back to the heuristics rather than handing the drafters the
offline fake (which returns non-JSON and fails). An explicitly injected ``llm_client``
is always honored.
"""

from __future__ import annotations

import logging

from app.config import Settings, get_settings
from app.domain.outreach import HeuristicOutreachDrafter, OutreachDrafter
from app.domain.tailoring import HeuristicMaterialsTailorer, MaterialsTailorer
from app.llm.client import LlmClient
from app.llm.drafting import LlmMaterialsTailorer, LlmOutreachDrafter
from app.llm.factory import create_llm_client

logger = logging.getLogger(__name__)


def create_drafters(
    settings: Settings | None = None,
    *,
    llm_client: LlmClient | None = None,
) -> tuple[MaterialsTailorer, OutreachDrafter]:
    """Return ``(tailorer, drafter)`` — LLM-backed when configured, else heuristic."""
    settings = settings or get_settings()

    if not settings.tailoring_llm_drafting:
        return HeuristicMaterialsTailorer(), HeuristicOutreachDrafter()

    if llm_client is None:
        if not settings.llm_enabled:
            logger.warning(
                "TAILORING_LLM_DRAFTING is on but LLM_ENABLED is off; "
                "falling back to the heuristic tailorer and drafter."
            )
            return HeuristicMaterialsTailorer(), HeuristicOutreachDrafter()
        llm_client = create_llm_client(settings)

    return LlmMaterialsTailorer(llm_client), LlmOutreachDrafter(llm_client)
