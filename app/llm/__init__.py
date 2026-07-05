"""LLM layer: the single boundary for all Anthropic Messages API calls.

Everything that talks to a model goes through here. Callers depend on the
:class:`LlmClient` protocol and pick a :class:`ModelTier` (never a model id); the concrete
model, timeouts, retries, and token/cost logging are resolved from config inside the
client. :func:`complete_json` adds strict-JSON parsing with a retry-once repair loop for
structured steps. The mock-first :class:`FakeLlmClient` is the default the factory returns,
so the whole pipeline runs offline with no API key.
"""

from __future__ import annotations

from app.llm.client import AnthropicClient, LlmClient
from app.llm.cost import CostTracker, ModelPrice
from app.llm.errors import (
    LlmConfigurationError,
    LlmError,
    LlmJsonError,
    LlmResponseError,
)
from app.llm.factory import create_llm_client
from app.llm.fake import FakeLlmClient
from app.llm.json_completion import complete_json, parse_json, strip_code_fences
from app.llm.matching import LlmJobReranker, LlmJobScorer
from app.llm.types import LlmMessage, LlmResponse, ModelTier, TokenUsage

__all__ = [
    "AnthropicClient",
    "CostTracker",
    "FakeLlmClient",
    "LlmClient",
    "LlmConfigurationError",
    "LlmError",
    "LlmJobReranker",
    "LlmJobScorer",
    "LlmJsonError",
    "LlmMessage",
    "LlmResponse",
    "LlmResponseError",
    "ModelPrice",
    "ModelTier",
    "TokenUsage",
    "complete_json",
    "create_llm_client",
    "parse_json",
    "strip_code_fences",
]
