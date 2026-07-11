"""Selects the problem-space detector from config (v3.1 Increments 7+8).

Mirrors ``create_story_synthesizer`` exactly: safe default first — the deterministic
:class:`~app.domain.problem_space.HeuristicProblemSpaceDetector` unless
``problem_space_llm_detection`` is on. Same footgun guard: the flag without a real
client (``llm_enabled=false`` and no injected client) logs a warning and stays
heuristic rather than handing detection the offline fake. An explicitly injected
``llm_client`` (e.g. a scripted fake in tests) is always honored. Either way the
detector only groups — the domain sanitizer, candidates, ids, and the bundle
validators stay deterministic.

Increment 8: pass ``grouping_store`` + ``user_id`` to wrap the **LLM** detector in
:class:`~app.domain.problem_space.PersistedGroupingDetector` — synthesis, eval, and
re-runs over unchanged problems replay one recorded partition (no drift, no repeat
LLM spend), namespaced by the prompt version so a prompt change never replays old
semantics. The heuristic is deterministic and free, so it is never wrapped — and a
heuristic partition must never be replayed as if the LLM had produced it.
"""

from __future__ import annotations

import logging

from app.config import Settings, get_settings
from app.domain.problem_space import (
    GroupingStore,
    HeuristicProblemSpaceDetector,
    PersistedGroupingDetector,
    ProblemSpaceDetector,
)
from app.llm.client import LlmClient
from app.llm.factory import create_llm_client
from app.llm.space_detection import GROUPING_PROMPT_VERSION, LlmProblemSpaceDetector

logger = logging.getLogger(__name__)


def create_problem_space_detector(
    settings: Settings | None = None,
    *,
    llm_client: LlmClient | None = None,
    grouping_store: GroupingStore | None = None,
    user_id: str | None = None,
) -> ProblemSpaceDetector:
    """Return the LLM problem-space detector when configured, else the heuristic."""
    settings = settings or get_settings()

    if not settings.problem_space_llm_detection:
        return HeuristicProblemSpaceDetector()

    if llm_client is None:
        if not settings.llm_enabled:
            logger.warning(
                "PROBLEM_SPACE_LLM_DETECTION is on but LLM_ENABLED is off; "
                "falling back to the heuristic problem-space detector."
            )
            return HeuristicProblemSpaceDetector()
        llm_client = create_llm_client(settings)

    detector: ProblemSpaceDetector = LlmProblemSpaceDetector(llm_client)
    if grouping_store is not None and user_id:
        detector = PersistedGroupingDetector(
            detector, grouping_store, user_id, version=GROUPING_PROMPT_VERSION
        )
    return detector
