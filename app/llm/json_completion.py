"""Strict-JSON completion on top of any :class:`LlmClient`.

Structured pipeline steps (PAR structuring, scoring rationales, outreach drafts) want a
machine-readable object, not prose. This module is the single place that enforces that:
send the messages, strip any ``` fences, ``json.loads`` the body, optionally validate,
and — on a parse/validation failure — retry **once** with a corrective nudge before
giving up with an :class:`LlmJsonError` that carries the offending raw text.

``validator`` lets callers parse into a typed value (e.g. a Pydantic model) and have its
exceptions count as a validation failure that triggers the retry.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from app.llm.client import LlmClient
from app.llm.errors import LlmJsonError
from app.llm.types import LlmMessage, ModelTier

# Matches a whole ```json ... ``` (or bare ``` ... ```) fenced block.
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL)

_RETRY_NUDGE = (
    "That was not valid JSON matching the required shape. Reply with ONLY the JSON "
    "value — no prose, no markdown code fences, no trailing commentary."
)


def strip_code_fences(text: str) -> str:
    """Return the inside of a fenced code block, or the trimmed text if unfenced."""
    stripped = text.strip()
    match = _FENCE_RE.match(stripped)
    return match.group(1).strip() if match else stripped


def parse_json(text: str) -> Any:
    """Strip fences and ``json.loads`` the result, raising :class:`LlmJsonError` on failure."""
    cleaned = strip_code_fences(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LlmJsonError(f"model output was not valid JSON: {exc}", raw_text=text) from exc


def complete_json[T](
    client: LlmClient,
    *,
    messages: list[LlmMessage],
    tier: ModelTier,
    system: str | None = None,
    validator: Callable[[Any], T] | None = None,
    max_tokens: int | None = None,
    max_parse_retries: int = 1,
) -> T:
    """Complete and return parsed (optionally validated) JSON.

    On a parse/validation failure the model's raw reply and a corrective instruction are
    appended to the conversation and it is asked again, up to ``max_parse_retries`` extra
    times. A validator's return value is what this returns; with no validator the parsed
    JSON is returned as-is (typed ``Any``).
    """
    convo = list(messages)
    last_error: LlmJsonError | None = None

    for attempt in range(max_parse_retries + 1):
        response = client.complete(
            messages=convo,
            tier=tier,
            system=system,
            max_tokens=max_tokens,
            # No sampling params: current models (Sonnet 5 / Opus 4.8) reject
            # non-default temperature with a 400 — verified live. The strict-JSON
            # system prompts + parse-retry are the structure guarantee instead.
        )
        try:
            parsed = parse_json(response.text)
            return validator(parsed) if validator is not None else parsed
        except LlmJsonError as exc:
            last_error = exc
        except Exception as exc:  # noqa: BLE001 - a validator rejecting the shape
            last_error = LlmJsonError(f"JSON failed validation: {exc}", raw_text=response.text)

        if attempt < max_parse_retries:
            convo = [
                *convo,
                LlmMessage.assistant(response.text),
                LlmMessage.user(_RETRY_NUDGE),
            ]

    assert last_error is not None  # loop runs at least once
    raise last_error
