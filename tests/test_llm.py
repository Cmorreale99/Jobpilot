"""Offline tests for the LLM layer: fakes, JSON completion, cost, factory, tier mapping.

Everything here runs with no ``anthropic`` install and no API key — the real client is
exercised only through an injected fake SDK object.
"""

from __future__ import annotations

import pytest
from app.config import Settings
from app.llm import (
    CostTracker,
    FakeLlmClient,
    LlmConfigurationError,
    LlmJsonError,
    LlmMessage,
    LlmResponseError,
    ModelTier,
    complete_json,
    create_llm_client,
    parse_json,
    strip_code_fences,
)
from app.llm.client import AnthropicClient
from app.llm.cost import ModelPrice, price_for
from app.llm.types import TokenUsage

# --- fence stripping / JSON parsing ----------------------------------------------


def test_strip_code_fences_json_block() -> None:
    assert strip_code_fences('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_strip_code_fences_bare_block() -> None:
    assert strip_code_fences("```\n[1, 2]\n```") == "[1, 2]"


def test_strip_code_fences_passthrough() -> None:
    assert strip_code_fences('  {"a": 1}  ') == '{"a": 1}'


def test_parse_json_handles_fenced_payload() -> None:
    assert parse_json('```json\n{"score": 0.5}\n```') == {"score": 0.5}


def test_parse_json_raises_on_garbage() -> None:
    with pytest.raises(LlmJsonError) as exc:
        parse_json("not json at all")
    assert exc.value.raw_text == "not json at all"


# --- FakeLlmClient ---------------------------------------------------------------


def test_fake_client_returns_queued_responses_in_order() -> None:
    client = FakeLlmClient(["first", "second"])
    r1 = client.complete(messages=[LlmMessage.user("a")], tier=ModelTier.BULK)
    r2 = client.complete(messages=[LlmMessage.user("b")], tier=ModelTier.DEEP)
    assert (r1.text, r2.text) == ("first", "second")
    assert r1.tier is ModelTier.BULK and r2.tier is ModelTier.DEEP
    assert client.call_count == 2
    assert client.calls[0].last_user_text == "a"


def test_fake_client_handler_and_default() -> None:
    client = FakeLlmClient(handler=lambda msgs, tier: f"{tier.value}:{msgs[-1].content}")
    assert client.complete(messages=[LlmMessage.user("hi")], tier=ModelTier.DEEP).text == "deep:hi"


def test_fake_client_simulated_failures() -> None:
    client = FakeLlmClient(["ok"], fail_times=1)
    with pytest.raises(LlmResponseError):
        client.complete(messages=[LlmMessage.user("x")], tier=ModelTier.BULK)
    assert client.complete(messages=[LlmMessage.user("x")], tier=ModelTier.BULK).text == "ok"


# --- complete_json ---------------------------------------------------------------


def test_complete_json_parses_first_try() -> None:
    client = FakeLlmClient(['```json\n{"fit": 0.9}\n```'])
    result = complete_json(client, messages=[LlmMessage.user("score")], tier=ModelTier.BULK)
    assert result == {"fit": 0.9}
    # Structured extraction is decoded deterministically.
    assert client.calls[0].temperature == 0.0


def test_complete_json_retries_once_then_succeeds() -> None:
    client = FakeLlmClient(["totally not json", '{"ok": true}'])
    result = complete_json(client, messages=[LlmMessage.user("go")], tier=ModelTier.DEEP)
    assert result == {"ok": True}
    assert client.call_count == 2
    # The repair turn carried the bad reply + a corrective nudge forward.
    assert client.calls[1].messages[-2].role == "assistant"
    assert client.calls[1].messages[-1].role == "user"


def test_complete_json_raises_after_exhausting_retries() -> None:
    client = FakeLlmClient(["nope", "still nope"])
    with pytest.raises(LlmJsonError):
        complete_json(client, messages=[LlmMessage.user("go")], tier=ModelTier.BULK)
    assert client.call_count == 2


def test_complete_json_validator_failure_triggers_retry() -> None:
    def require_score(value: object) -> float:
        if not isinstance(value, dict) or "score" not in value:
            raise ValueError("missing score")
        return float(value["score"])

    client = FakeLlmClient(['{"wrong": 1}', '{"score": 0.7}'])
    result = complete_json(
        client,
        messages=[LlmMessage.user("go")],
        tier=ModelTier.BULK,
        validator=require_score,
    )
    assert result == 0.7
    assert client.call_count == 2


def test_complete_json_no_retry_budget() -> None:
    client = FakeLlmClient(["bad"])
    with pytest.raises(LlmJsonError):
        complete_json(
            client,
            messages=[LlmMessage.user("go")],
            tier=ModelTier.BULK,
            max_parse_retries=0,
        )
    assert client.call_count == 1


# --- cost tracking ---------------------------------------------------------------


def test_price_for_matches_by_substring() -> None:
    assert price_for("claude-opus-4-8").output_per_mtok == 75.0
    assert price_for("claude-sonnet-5").input_per_mtok == 3.0


def test_price_for_unknown_uses_fallback() -> None:
    assert price_for("some-unknown-model").input_per_mtok == 15.0


def test_cost_tracker_accumulates() -> None:
    tracker = CostTracker({"sonnet": ModelPrice(3.0, 15.0)})
    cost = tracker.record("claude-sonnet-5", TokenUsage(input_tokens=1_000_000, output_tokens=0))
    assert cost == pytest.approx(3.0)
    tracker.record("claude-sonnet-5", TokenUsage(input_tokens=0, output_tokens=1_000_000))
    assert tracker.total_cost_usd == pytest.approx(18.0)
    assert tracker.calls == 2
    assert tracker.usage.total_tokens == 2_000_000


# --- factory ---------------------------------------------------------------------


def test_factory_returns_fake_by_default() -> None:
    assert isinstance(create_llm_client(Settings()), FakeLlmClient)


def test_factory_real_client_requires_api_key() -> None:
    with pytest.raises(LlmConfigurationError):
        create_llm_client(Settings(llm_enabled=True))


# --- AnthropicClient with an injected fake SDK -----------------------------------


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeMessage:
    def __init__(self, text: str) -> None:
        self.content = [_FakeBlock(text)]
        self.usage = _FakeUsage(10, 20)
        self.stop_reason = "end_turn"


class _FakeMessages:
    def __init__(self, message: _FakeMessage) -> None:
        self._message = message
        self.last_kwargs: dict[str, object] = {}

    def create(self, **kwargs: object) -> _FakeMessage:
        self.last_kwargs = kwargs
        return self._message


class _FakeSDK:
    def __init__(self, text: str) -> None:
        self.messages = _FakeMessages(_FakeMessage(text))


def _settings() -> Settings:
    return Settings(
        anthropic_api_key="sk-test",
        anthropic_model_bulk="claude-sonnet-5",
        anthropic_model_deep="claude-opus-4-8",
    )


def test_anthropic_client_maps_tier_to_model_and_records_cost() -> None:
    sdk = _FakeSDK('{"ok": 1}')
    tracker = CostTracker()
    client = AnthropicClient(_settings(), client=sdk, cost_tracker=tracker)  # type: ignore[arg-type]

    resp = client.complete(messages=[LlmMessage.user("hi")], tier=ModelTier.DEEP, system="be terse")

    assert resp.text == '{"ok": 1}'
    assert resp.model == "claude-opus-4-8"
    assert resp.usage == TokenUsage(input_tokens=10, output_tokens=20)
    assert sdk.messages.last_kwargs["model"] == "claude-opus-4-8"
    assert sdk.messages.last_kwargs["system"] == "be terse"
    assert tracker.calls == 1


def test_anthropic_client_bulk_tier_uses_bulk_model() -> None:
    sdk = _FakeSDK("ok")
    client = AnthropicClient(_settings(), client=sdk)  # type: ignore[arg-type]
    client.complete(messages=[LlmMessage.user("hi")], tier=ModelTier.BULK)
    assert sdk.messages.last_kwargs["model"] == "claude-sonnet-5"


def test_anthropic_client_wraps_sdk_errors() -> None:
    class _Boom:
        def create(self, **kwargs: object) -> object:
            raise RuntimeError("network down")

    class _BoomSDK:
        messages = _Boom()

    client = AnthropicClient(_settings(), client=_BoomSDK())  # type: ignore[arg-type]
    with pytest.raises(LlmResponseError):
        client.complete(messages=[LlmMessage.user("hi")], tier=ModelTier.BULK)


def test_anthropic_client_empty_messages_rejected() -> None:
    client = AnthropicClient(_settings(), client=_FakeSDK("ok"))  # type: ignore[arg-type]
    with pytest.raises(LlmResponseError):
        client.complete(messages=[], tier=ModelTier.BULK)
