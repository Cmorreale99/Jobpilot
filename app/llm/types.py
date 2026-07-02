"""Shared value types for the LLM layer.

Small, immutable, provider-agnostic. Nothing here imports the Anthropic SDK, so these
types are safe to use anywhere (services, tests) without pulling in a real dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

Role = Literal["user", "assistant"]


class ModelTier(StrEnum):
    """Which configured model a call should use.

    Callers pick a *tier*, never a model id — the concrete model is resolved from config
    at the client, so models stay env-driven and are never hardcoded at call sites.
    """

    BULK = "bulk"  # cheap/fast: stage-1 scoring, bulk extraction
    DEEP = "deep"  # strong: top-N re-rank, outreach drafting, prep packets


@dataclass(frozen=True)
class LlmMessage:
    """One turn in a conversation sent to the model."""

    role: Role
    content: str

    @classmethod
    def user(cls, content: str) -> LlmMessage:
        return cls(role="user", content=content)

    @classmethod
    def assistant(cls, content: str) -> LlmMessage:
        return cls(role="assistant", content=content)


@dataclass(frozen=True)
class TokenUsage:
    """Input/output token counts reported for a single call."""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


@dataclass(frozen=True)
class LlmResponse:
    """A completed model response with the metadata the layer needs for logging."""

    text: str
    model: str
    tier: ModelTier
    usage: TokenUsage = TokenUsage()
    stop_reason: str | None = None
