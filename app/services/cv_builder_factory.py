"""Selects the Master CV builder's PAR structurer from config.

The service layer may import ``app/llm/`` (only ``domain/`` may not), so this is where the
heuristic-vs-LLM structuring choice is resolved. Mirrors the other factories: safe default
first — the deterministic :class:`HeuristicClaimStructurer` unless
``master_cv_llm_structuring`` is on.

Footgun guard: turning the flag on without a real client (``llm_enabled=false`` and no
injected client) would hand the structurer the offline fake, which returns non-JSON and
fails. So in that case we log and fall back to the heuristic rather than crash ingestion.
An explicitly injected ``llm_client`` (e.g. a scripted fake in tests) is always honored.
"""

from __future__ import annotations

import logging

from app.config import Settings, get_settings
from app.domain.cv import MasterCvBuilder
from app.llm.claim_structurer import LlmClaimStructurer
from app.llm.client import LlmClient
from app.llm.factory import create_llm_client

logger = logging.getLogger(__name__)


def create_cv_builder(
    settings: Settings | None = None,
    *,
    llm_client: LlmClient | None = None,
) -> MasterCvBuilder:
    """Return a builder using the LLM structurer when configured, else the heuristic."""
    settings = settings or get_settings()

    if not settings.master_cv_llm_structuring:
        return MasterCvBuilder()

    if llm_client is None:
        if not settings.llm_enabled:
            logger.warning(
                "MASTER_CV_LLM_STRUCTURING is on but LLM_ENABLED is off; "
                "falling back to the heuristic PAR structurer."
            )
            return MasterCvBuilder()
        llm_client = create_llm_client(settings)

    return MasterCvBuilder(structurer=LlmClaimStructurer(llm_client))
