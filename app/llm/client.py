"""The :class:`LlmClient` interface and the real Anthropic-backed implementation.

Consumers depend only on the :class:`LlmClient` protocol and pick a :class:`ModelTier`;
the concrete model id is resolved from config here, so model choices stay env-driven.
The Anthropic SDK is imported lazily inside :class:`AnthropicClient`, so importing this
module (and the whole app) requires neither the ``anthropic`` package nor an API key —
the mock-first fake in :mod:`app.llm.fake` is the default the factory hands out.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from app.config import Settings
from app.llm.cost import CostTracker
from app.llm.errors import LlmConfigurationError, LlmResponseError
from app.llm.types import LlmMessage, LlmResponse, ModelTier, TokenUsage

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime here
    from anthropic import Anthropic


class LlmClient(Protocol):
    """A synchronous text-completion client, tier-addressed.

    Kept sync to match the matching/service layer that consumes it (the domain
    ``JobScorer``/``JobReranker`` and ``ClaimStructurer`` protocols are all sync).
    """

    def complete(
        self,
        *,
        messages: list[LlmMessage],
        tier: ModelTier,
        system: str | None = None,
        cached_context: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LlmResponse: ...


class AnthropicClient:
    """Real client over the Anthropic Messages API.

    Timeouts and transient-error retries are delegated to the SDK (configured from
    settings); each successful call's token usage is recorded on the :class:`CostTracker`.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        client: Anthropic | None = None,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        self._settings = settings
        self._cost = cost_tracker or CostTracker()
        self._client = client if client is not None else self._build_client(settings)

    @staticmethod
    def _build_client(settings: Settings) -> Anthropic:
        if not settings.anthropic_api_key:
            raise LlmConfigurationError(
                "ANTHROPIC_API_KEY is not set; cannot build the real LLM client. "
                "Leave LLM_ENABLED=false to use the mock-first fake."
            )
        try:
            from anthropic import Anthropic
        except ImportError as exc:  # pragma: no cover - depends on optional install
            raise LlmConfigurationError(
                "The 'anthropic' package is not installed; run `uv sync`."
            ) from exc
        return Anthropic(
            api_key=settings.anthropic_api_key,
            timeout=settings.anthropic_timeout_seconds,
            max_retries=settings.anthropic_max_retries,
        )

    @property
    def cost(self) -> CostTracker:
        return self._cost

    def model_for(self, tier: ModelTier) -> str:
        model = (
            self._settings.anthropic_model_deep
            if tier is ModelTier.DEEP
            else self._settings.anthropic_model_bulk
        )
        if not model:
            raise LlmConfigurationError(f"No model configured for tier {tier.value!r}.")
        return model

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
        if not messages:
            raise LlmResponseError("complete() requires at least one message.")
        model = self.model_for(tier)
        # Only the arguments we actually set are passed; the SDK applies its own defaults
        # for the rest. Typed loosely because it is unpacked into the SDK's overloads.
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens or self._settings.anthropic_max_tokens,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if cached_context is not None:
            # Prompt caching is a prefix match over tools -> system -> messages, so the
            # stable evidence block goes last in `system` with the cache breakpoint on it
            # — one marker caches the whole system prefix. Below the model's minimum
            # cacheable prefix the marker is silently ignored, so this is always safe.
            blocks: list[dict[str, Any]] = []
            if system is not None:
                blocks.append({"type": "text", "text": system})
            blocks.append(
                {
                    "type": "text",
                    "text": cached_context,
                    "cache_control": {"type": "ephemeral"},
                }
            )
            kwargs["system"] = blocks
        elif system is not None:
            kwargs["system"] = system
        if temperature is not None:
            kwargs["temperature"] = temperature

        try:
            response = self._client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - normalize every SDK/transport failure
            raise LlmResponseError(f"Anthropic API call failed: {exc}") from exc

        usage = _extract_usage(response)
        self._cost.record(model, usage)
        return LlmResponse(
            text=_extract_text(response),
            model=model,
            tier=tier,
            usage=usage,
            stop_reason=getattr(response, "stop_reason", None),
        )


def _extract_text(response: object) -> str:
    """Concatenate the text blocks of a Messages API response."""
    content = getattr(response, "content", None)
    if not content:
        raise LlmResponseError("Anthropic response had no content blocks.")
    parts: list[str] = []
    for block in content:
        if getattr(block, "type", None) == "text":
            parts.append(str(getattr(block, "text", "")))
    text = "".join(parts).strip()
    if not text:
        raise LlmResponseError("Anthropic response contained no text blocks.")
    return text


def _extract_usage(response: object) -> TokenUsage:
    usage = getattr(response, "usage", None)
    if usage is None:
        return TokenUsage()
    return TokenUsage(
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        cache_creation_input_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
        cache_read_input_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
    )
