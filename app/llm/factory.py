"""Selects the LLM client: mock-first fake by default, real Anthropic when enabled.

Mirrors the Drive/GitHub factories — ``create_llm_client()`` returns the deterministic
fake unless ``LLM_ENABLED=true``, so the pipeline and tests run offline with no API key.
"""

from __future__ import annotations

from app.config import Settings, get_settings
from app.llm.client import AnthropicClient, LlmClient
from app.llm.cost import CostTracker
from app.llm.fake import FakeLlmClient


def create_llm_client(
    settings: Settings | None = None,
    *,
    cost_tracker: CostTracker | None = None,
) -> LlmClient:
    """Return the real client when ``llm_enabled`` is set, else the fixture-safe fake."""
    settings = settings or get_settings()
    if settings.llm_enabled:
        return AnthropicClient(settings, cost_tracker=cost_tracker)
    return FakeLlmClient()
