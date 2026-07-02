"""Error taxonomy for the LLM layer.

All failures raised out of ``app/llm/`` are subclasses of :class:`LlmError`, so callers
(services, LLM-backed structurers/scorers) can catch one type and fall back or surface a
clean message without importing the Anthropic SDK.
"""

from __future__ import annotations


class LlmError(Exception):
    """Base class for every error raised by the LLM layer."""


class LlmConfigurationError(LlmError):
    """The LLM client is misconfigured (e.g. real client selected with no API key)."""


class LlmResponseError(LlmError):
    """The Messages API call failed (transport error, non-retryable API error, empty body)."""


class LlmJsonError(LlmError):
    """A structured-JSON completion could not be parsed/validated after the retry budget.

    Carries the last raw model text so callers can log what actually came back.
    """

    def __init__(self, message: str, *, raw_text: str = "") -> None:
        super().__init__(message)
        self.raw_text = raw_text
