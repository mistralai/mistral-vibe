from __future__ import annotations

from collections.abc import Callable

import pytest

from vibe.core.config import VibeConfigSchema
from vibe.core.config.models import ModelConfig
from vibe.core.middleware import (
    AutoCompactMiddleware,
    ContextWarningMiddleware,
    ConversationContext,
    MiddlewareAction,
)
from vibe.core.types import AgentStats, MessageList

pytestmark = pytest.mark.asyncio


def _context(config: VibeConfigSchema, context_tokens: int) -> ConversationContext:
    return ConversationContext(
        messages=MessageList(),
        stats=AgentStats(context_tokens=context_tokens),
        config=config,
    )


class TestContextWarningReportsTheCompactionPoint:
    """The warning reports the budget the conversation actually runs under."""

    async def test_derived_threshold_reports_the_derived_value(
        self, make_config: Callable[..., VibeConfigSchema]
    ) -> None:
        model = ModelConfig(
            name="m", provider="p", alias="m", max_context_length=262_144
        )
        config = make_config(models=[model], active_model="m")

        result = await ContextWarningMiddleware().before_turn(_context(config, 104_858))

        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert result.message is not None
        assert "104,858/209,715 tokens" in result.message
        assert "50% of your context budget" in result.message

    async def test_explicit_cap_reports_the_cap_not_the_window(
        self, make_config: Callable[..., VibeConfigSchema]
    ) -> None:
        # A user holding a 262K model to 20K must not be told they have
        # capacity they deliberately gave up.
        model = ModelConfig(
            name="m",
            provider="p",
            alias="m",
            max_context_length=262_144,
            auto_compact_threshold=20_000,
        )
        config = make_config(models=[model], active_model="m")

        result = await ContextWarningMiddleware().before_turn(_context(config, 10_000))

        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert result.message is not None
        assert "10,000/20,000 tokens" in result.message

    async def test_model_without_a_window_reports_the_global(
        self, make_config: Callable[..., VibeConfigSchema]
    ) -> None:
        model = ModelConfig(name="m", provider="p", alias="m")
        config = make_config(models=[model], active_model="m")

        result = await ContextWarningMiddleware().before_turn(_context(config, 100_000))

        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert result.message is not None
        assert "100,000/200,000 tokens" in result.message

    async def test_does_not_warn_just_below_the_trigger(
        self, make_config: Callable[..., VibeConfigSchema]
    ) -> None:
        model = ModelConfig(
            name="m", provider="p", alias="m", max_context_length=262_144
        )
        config = make_config(models=[model], active_model="m")

        result = await ContextWarningMiddleware().before_turn(_context(config, 104_857))

        assert result.action == MiddlewareAction.CONTINUE

    async def test_zero_threshold_never_warns(
        self, make_config: Callable[..., VibeConfigSchema]
    ) -> None:
        model = ModelConfig(name="m", provider="p", alias="m", auto_compact_threshold=0)
        config = make_config(models=[model], active_model="m")

        result = await ContextWarningMiddleware().before_turn(_context(config, 10**9))

        assert result.action == MiddlewareAction.CONTINUE

    async def test_warns_once_per_session(
        self, make_config: Callable[..., VibeConfigSchema]
    ) -> None:
        model = ModelConfig(name="m", provider="p", alias="m")
        config = make_config(models=[model], active_model="m")
        middleware = ContextWarningMiddleware()

        first = await middleware.before_turn(_context(config, 150_000))
        second = await middleware.before_turn(_context(config, 160_000))

        assert first.action == MiddlewareAction.INJECT_MESSAGE
        assert second.action == MiddlewareAction.CONTINUE


class TestAutoCompactUsesTheResolvedThreshold:
    @pytest.mark.parametrize(
        ("context_tokens", "expected"),
        [(209_714, MiddlewareAction.CONTINUE), (209_715, MiddlewareAction.COMPACT)],
    )
    async def test_compacts_at_eighty_percent_of_the_window(
        self,
        make_config: Callable[..., VibeConfigSchema],
        context_tokens: int,
        expected: MiddlewareAction,
    ) -> None:
        model = ModelConfig(
            name="m", provider="p", alias="m", max_context_length=262_144
        )
        config = make_config(models=[model], active_model="m")

        result = await AutoCompactMiddleware().before_turn(
            _context(config, context_tokens)
        )

        assert result.action == expected

    async def test_never_compacts_on_a_zero_cap(
        self, make_config: Callable[..., VibeConfigSchema]
    ) -> None:
        model = ModelConfig(name="m", provider="p", alias="m", auto_compact_threshold=0)
        config = make_config(models=[model], active_model="m")

        result = await AutoCompactMiddleware().before_turn(_context(config, 10**9))

        assert result.action == MiddlewareAction.CONTINUE

    async def test_a_zero_global_disables_compaction_and_the_warning(
        self, make_config: Callable[..., VibeConfigSchema]
    ) -> None:
        model = ModelConfig(name="m", provider="p", alias="m")
        config = make_config(auto_compact_threshold=0, models=[model], active_model="m")

        compact = await AutoCompactMiddleware().before_turn(_context(config, 10**9))
        warning = await ContextWarningMiddleware().before_turn(_context(config, 10**9))

        assert compact.action == MiddlewareAction.CONTINUE
        assert warning.action == MiddlewareAction.CONTINUE
