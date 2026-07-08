"""Selects the story synthesizer from config.

Mirrors the other LLM factories (``extractor_factory`` et al.): safe default first — the
deterministic :class:`~app.domain.project_story.HeuristicStorySynthesizer` unless
``story_llm_synthesis`` is on. Same footgun guard: the flag without a real client
(``llm_enabled=false`` and no injected client) logs a warning and stays heuristic rather
than handing the synthesizer the offline fake. An explicitly injected ``llm_client`` (e.g. a
scripted fake in tests) is always honored. Either way ``validate_story_structure`` + the
readiness gate downstream gate every synthesized draft.
"""

from __future__ import annotations

import logging

from app.config import Settings, get_settings
from app.domain.project_story import HeuristicStorySynthesizer, StorySynthesizer
from app.llm.client import LlmClient
from app.llm.factory import create_llm_client
from app.llm.story_synthesis import LlmStorySynthesizer

logger = logging.getLogger(__name__)


def create_story_synthesizer(
    settings: Settings | None = None,
    *,
    llm_client: LlmClient | None = None,
) -> StorySynthesizer:
    """Return the LLM story synthesizer when configured, else the heuristic."""
    settings = settings or get_settings()

    if not settings.story_llm_synthesis:
        return HeuristicStorySynthesizer()

    if llm_client is None:
        if not settings.llm_enabled:
            logger.warning(
                "STORY_LLM_SYNTHESIS is on but LLM_ENABLED is off; "
                "falling back to the heuristic story synthesizer."
            )
            return HeuristicStorySynthesizer()
        llm_client = create_llm_client(settings)

    return LlmStorySynthesizer(llm_client)
