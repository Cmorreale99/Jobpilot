"""Selects the roster proposer and chunk assigner from config.

Mirrors the other LLM factories: deterministic defaults unless ``roster_llm_detection``
is on, with the same footgun guard — the flag without a real client logs a warning and
stays heuristic. An explicitly injected ``llm_client`` (a scripted fake in tests) is
always honored.
"""

from __future__ import annotations

import logging

from app.config import Settings, get_settings
from app.domain.roster import (
    ChunkAssigner,
    HeuristicChunkAssigner,
    HeuristicRosterProposer,
    HeuristicSectionAssigner,
    RosterProposer,
    SectionAssigner,
)
from app.llm.client import LlmClient
from app.llm.factory import create_llm_client
from app.llm.roster import LlmChunkAssigner, LlmRosterProposer, LlmSectionAssigner

logger = logging.getLogger(__name__)


def _resolve_client(settings: Settings, llm_client: LlmClient | None) -> LlmClient | None:
    if llm_client is not None:
        return llm_client
    if not settings.llm_enabled:
        logger.warning(
            "ROSTER_LLM_DETECTION is on but LLM_ENABLED is off; "
            "falling back to the heuristic roster proposer/assigner."
        )
        return None
    return create_llm_client(settings)


def create_roster_proposer(
    settings: Settings | None = None,
    *,
    llm_client: LlmClient | None = None,
) -> RosterProposer:
    """Return the LLM roster proposer when configured, else the heuristic."""
    settings = settings or get_settings()
    if not settings.roster_llm_detection:
        return HeuristicRosterProposer()
    client = _resolve_client(settings, llm_client)
    return LlmRosterProposer(client) if client is not None else HeuristicRosterProposer()


def create_chunk_assigner(
    settings: Settings | None = None,
    *,
    llm_client: LlmClient | None = None,
) -> ChunkAssigner:
    """Return the LLM chunk assigner when configured, else the heuristic."""
    settings = settings or get_settings()
    if not settings.roster_llm_detection:
        return HeuristicChunkAssigner()
    client = _resolve_client(settings, llm_client)
    return LlmChunkAssigner(client) if client is not None else HeuristicChunkAssigner()


def create_section_assigner(
    settings: Settings | None = None,
    *,
    llm_client: LlmClient | None = None,
) -> SectionAssigner:
    """Return the LLM section assigner when configured, else the heuristic (H5)."""
    settings = settings or get_settings()
    if not settings.roster_llm_detection:
        return HeuristicSectionAssigner()
    client = _resolve_client(settings, llm_client)
    return LlmSectionAssigner(client) if client is not None else HeuristicSectionAssigner()
