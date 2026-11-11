"""Shared run-local admission for completion token and price budgets."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import math
from typing import Literal

from mistralai_vibe_local_harness.protocol import RustTokenUsage

BudgetStopReason = Literal["token_limit", "price_limit", "pricing_unavailable"]


@dataclass(frozen=True, slots=True)
class CompletionPricing:
    """One completion route's USD prices per million tokens."""

    input_per_million: float
    output_per_million: float
    cached_input_per_million: float | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("input_per_million", self.input_per_million),
            ("output_per_million", self.output_per_million),
            ("cached_input_per_million", self.cached_input_per_million),
        ):
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError(f"{name} must be finite and nonnegative")

    def cost(self, usage: RustTokenUsage) -> float:
        cached = min(usage.input_tokens, usage.cached_input_tokens)
        cached_price = (
            self.input_per_million
            if self.cached_input_per_million is None
            else self.cached_input_per_million
        )
        return (
            (usage.input_tokens - cached) * self.input_per_million
            + cached * cached_price
            + usage.output_tokens * self.output_per_million
        ) / 1_000_000


class CompletionBudgetAccountingDeferred(Exception):
    """A provider-accepted failure whose replacement result reports usage."""


class CompletionBudgetDenied(RuntimeError):
    """A completion that the shared completion budget did not admit."""

    def __init__(self, reason: BudgetStopReason) -> None:
        super().__init__(reason.replace("_", " ").capitalize())
        self.reason = reason


class SharedCompletionBudget:
    """Coordinate measured completion spend across one root session tree.

    Admission happens once, before a completion starts; its provider retries
    always run to the end. Completions admitted before a sibling closes the
    budget may finish and add overshoot. A new headless invocation receives a
    new instance. It meters the completions a Runtime runs; a host's own model
    calls outside it are not counted.
    """

    def __init__(
        self, *, max_total_tokens: int | None = None, max_price: float | None = None
    ) -> None:
        if max_total_tokens is None and max_price is None:
            raise ValueError("a shared completion budget needs at least one limit")
        if max_total_tokens is not None and max_total_tokens < 0:
            raise ValueError("max_total_tokens must be nonnegative")
        if max_price is not None and (not math.isfinite(max_price) or max_price < 0):
            raise ValueError("max_price must be finite and nonnegative")
        self.max_total_tokens = max_total_tokens
        self.max_price = max_price
        self._total_tokens = 0
        self._price = 0.0
        self._reason: BudgetStopReason | None = None
        self._lock = asyncio.Lock()

    async def admission_reason(
        self, pricing: CompletionPricing | None
    ) -> BudgetStopReason | None:
        """Return why another completion is forbidden, or None to admit it."""
        async with self._lock:
            if self._reason is None:
                self._reason = self._limit_reason()
            if self._reason is None and self.max_price is not None and pricing is None:
                self._reason = "pricing_unavailable"
            return self._reason

    async def record(
        self, usage: RustTokenUsage, pricing: CompletionPricing | None
    ) -> None:
        """Add one provider attempt's measured usage to the tree-wide totals."""
        async with self._lock:
            self._total_tokens += usage.total_tokens
            if self.max_price is not None:
                if pricing is None:
                    if self._reason is None:
                        self._reason = "pricing_unavailable"
                    return
                self._price += pricing.cost(usage)
            if self._reason is None:
                self._reason = self._limit_reason()

    def _limit_reason(self) -> Literal["token_limit", "price_limit"] | None:
        if (
            self.max_total_tokens is not None
            and self._total_tokens >= self.max_total_tokens
        ):
            return "token_limit"
        if self.max_price is not None and self._price_reached(self.max_price):
            return "price_limit"
        return None

    def _price_reached(self, max_price: float) -> bool:
        if max_price == 0:
            # A zero ceiling admits calls until one costs anything.
            return self._price > 0
        return self._price >= max_price


__all__ = [
    "BudgetStopReason",
    "CompletionBudgetAccountingDeferred",
    "CompletionBudgetDenied",
    "CompletionPricing",
    "SharedCompletionBudget",
]
