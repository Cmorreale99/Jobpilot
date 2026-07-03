"""Deterministic fake :class:`LlmClient` — the mock-first default.

Records every call and returns scripted responses, so the full pipeline (and every test)
runs offline with no API key and no ``anthropic`` install. Two ways to script replies:

* ``responses`` — a queue consumed in order (each call pops the next).
* ``handler`` — a callable that computes a reply from the request.

With neither, it echoes a canned string. Set ``fail_times`` to raise
:class:`LlmResponseError` on the first N calls (to exercise retry/fallback paths).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable

from app.llm.errors import LlmResponseError
from app.llm.types import LlmMessage, LlmResponse, ModelTier, TokenUsage

Handler = Callable[[list[LlmMessage], ModelTier], str]


class RecordedCall:
    """One captured invocation of :meth:`FakeLlmClient.complete`."""

    def __init__(
        self,
        messages: list[LlmMessage],
        tier: ModelTier,
        system: str | None,
        cached_context: str | None,
        max_tokens: int | None,
        temperature: float | None,
    ) -> None:
        self.messages = messages
        self.tier = tier
        self.system = system
        self.cached_context = cached_context
        self.max_tokens = max_tokens
        self.temperature = temperature

    @property
    def last_user_text(self) -> str:
        for message in reversed(self.messages):
            if message.role == "user":
                return message.content
        return ""


class FakeLlmClient:
    """A scriptable, deterministic stand-in for :class:`LlmClient`."""

    def __init__(
        self,
        responses: list[str] | None = None,
        *,
        handler: Handler | None = None,
        default_text: str = "fake-llm-response",
        model_label: str = "fake-model",
        usage: TokenUsage | None = None,
        fail_times: int = 0,
    ) -> None:
        self._responses: deque[str] = deque(responses or [])
        self._handler = handler
        self._default_text = default_text
        self._model_label = model_label
        self._usage = usage or TokenUsage(input_tokens=1, output_tokens=1)
        self._remaining_failures = fail_times
        self.calls: list[RecordedCall] = []

    def complete(
        self,
        *,
        messages: list[LlmMessage],
        tier: ModelTier,
        system: str | None = None,
        cached_context: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LlmResponse:
        self.calls.append(
            RecordedCall(messages, tier, system, cached_context, max_tokens, temperature)
        )
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise LlmResponseError("fake client: simulated transient failure")

        if self._handler is not None:
            text = self._handler(messages, tier)
        elif self._responses:
            text = self._responses.popleft()
        else:
            text = self._default_text

        return LlmResponse(
            text=text,
            model=f"{self._model_label}-{tier.value}",
            tier=tier,
            usage=self._usage,
        )

    @property
    def call_count(self) -> int:
        return len(self.calls)
