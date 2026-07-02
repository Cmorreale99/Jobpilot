"""Token/cost accounting for LLM calls.

Every real call routes its usage through a :class:`CostTracker`, which logs per-call
token counts and an *estimated* USD cost and accumulates process-wide totals. Prices are
estimates keyed by a substring of the model id (``opus``/``sonnet``/``haiku``) so they
survive model-id changes; override :data:`DEFAULT_PRICES` or pass a custom table when the
real per-model numbers are known. Cost logging is a guardrail (spot runaway spend), not a
billing system — treat the numbers as indicative.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from app.llm.types import TokenUsage

logger = logging.getLogger("app.llm.cost")


@dataclass(frozen=True)
class ModelPrice:
    """USD per **million** tokens, in and out."""

    input_per_mtok: float
    output_per_mtok: float

    def cost(self, usage: TokenUsage) -> float:
        return (
            usage.input_tokens * self.input_per_mtok + usage.output_tokens * self.output_per_mtok
        ) / 1_000_000


# Public prices as of 2026-07 (per MTok), matched by substring against the model id.
# Opus 4.x: $5/$25; Sonnet 4.6/5: $3/$15 (intro $2/$10 through 2026-08); Haiku 4.5: $1/$5;
# Fable 5: $10/$50. A conservative fallback is used so an unknown model never logs $0.
DEFAULT_PRICES: dict[str, ModelPrice] = {
    "fable": ModelPrice(input_per_mtok=10.0, output_per_mtok=50.0),
    "opus": ModelPrice(input_per_mtok=5.0, output_per_mtok=25.0),
    "sonnet": ModelPrice(input_per_mtok=3.0, output_per_mtok=15.0),
    "haiku": ModelPrice(input_per_mtok=1.0, output_per_mtok=5.0),
}
_FALLBACK_PRICE = ModelPrice(input_per_mtok=10.0, output_per_mtok=50.0)


def price_for(model: str, prices: dict[str, ModelPrice] | None = None) -> ModelPrice:
    """Resolve a price for a model id by substring match, else the conservative fallback."""
    table = prices if prices is not None else DEFAULT_PRICES
    lowered = model.lower()
    for key, price in table.items():
        if key in lowered:
            return price
    return _FALLBACK_PRICE


class CostTracker:
    """Accumulates token usage and estimated cost across calls (thread-safe)."""

    def __init__(self, prices: dict[str, ModelPrice] | None = None) -> None:
        self._prices = prices
        self._lock = threading.Lock()
        self._usage = TokenUsage()
        self._cost_usd = 0.0
        self._calls = 0

    def record(self, model: str, usage: TokenUsage) -> float:
        """Record one call's usage; return its estimated cost and log the line."""
        cost = price_for(model, self._prices).cost(usage)
        with self._lock:
            self._usage += usage
            self._cost_usd += cost
            self._calls += 1
        logger.info(
            "llm call model=%s in=%d out=%d est_cost=$%.4f",
            model,
            usage.input_tokens,
            usage.output_tokens,
            cost,
        )
        return cost

    @property
    def calls(self) -> int:
        with self._lock:
            return self._calls

    @property
    def usage(self) -> TokenUsage:
        with self._lock:
            return self._usage

    @property
    def total_cost_usd(self) -> float:
        with self._lock:
            return self._cost_usd
