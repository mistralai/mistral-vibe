from __future__ import annotations

import pytest

from vibe.core.config import SessionLoggingConfig, VibeConfig
from vibe.core.middleware import (
    PLAN_MODE_REMINDER,
    ConversationContext,
    MiddlewareAction,
    MiddlewarePipeline,
    PlanModeMiddleware,
)
from vibe.core.modes import AgentMode
from vibe.core.types import AgentStats


def make_context() -> ConversationContext:
    config = VibeConfig(session_logging=SessionLoggingConfig(enabled=False))
    return ConversationContext(messages=[], stats=AgentStats(), config=config)


class TestPlanModeMiddleware:
    @pytest.mark.asyncio
    async def test_injects_reminder_when_plan_mode_active(self) -> None:
        middleware = PlanModeMiddleware(lambda: AgentMode.PLAN)
        ctx = make_context()

        result = await middleware.before_turn(ctx)

        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert result.message == PLAN_MODE_REMINDER

    @pytest.mark.asyncio
    async def test_does_not_inject_when_default_mode(self) -> None:
        middleware = PlanModeMiddleware(lambda: AgentMode.DEFAULT)
        ctx = make_context()

        result = await middleware.before_turn(ctx)

        assert result.action == MiddlewareAction.CONTINUE
        assert result.message is None

    @pytest.mark.asyncio
    async def test_does_not_inject_when_auto_approve_mode(self) -> None:
        middleware = PlanModeMiddleware(lambda: AgentMode.AUTO_APPROVE)
        ctx = make_context()

        result = await middleware.before_turn(ctx)

        assert result.action == MiddlewareAction.CONTINUE
        assert result.message is None

    @pytest.mark.asyncio
    async def test_does_not_inject_when_accept_edits_mode(self) -> None:
        middleware = PlanModeMiddleware(lambda: AgentMode.ACCEPT_EDITS)
        ctx = make_context()

        result = await middleware.before_turn(ctx)

        assert result.action == MiddlewareAction.CONTINUE
        assert result.message is None

    @pytest.mark.asyncio
    async def test_after_turn_always_continues(self) -> None:
        middleware = PlanModeMiddleware(lambda: AgentMode.PLAN)
        ctx = make_context()

        result = await middleware.after_turn(ctx)

        assert result.action == MiddlewareAction.CONTINUE

    @pytest.mark.asyncio
    async def test_dynamically_checks_mode(self) -> None:
        current_mode = AgentMode.DEFAULT
        middleware = PlanModeMiddleware(lambda: current_mode)
        ctx = make_context()

        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.CONTINUE

        current_mode = AgentMode.PLAN
        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.INJECT_MESSAGE

        current_mode = AgentMode.AUTO_APPROVE
        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.CONTINUE

    @pytest.mark.asyncio
    async def test_custom_reminder(self) -> None:
        custom_reminder = "Custom plan mode reminder"
        middleware = PlanModeMiddleware(
            lambda: AgentMode.PLAN, reminder=custom_reminder
        )
        ctx = make_context()

        result = await middleware.before_turn(ctx)

        assert result.message == custom_reminder

    def test_reset_does_nothing(self) -> None:
        middleware = PlanModeMiddleware(lambda: AgentMode.PLAN)
        middleware.reset()


class TestMiddlewarePipelineWithPlanMode:
    @pytest.mark.asyncio
    async def test_pipeline_includes_plan_mode_injection(self) -> None:
        pipeline = MiddlewarePipeline()
        pipeline.add(PlanModeMiddleware(lambda: AgentMode.PLAN))
        ctx = make_context()

        result = await pipeline.run_before_turn(ctx)

        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert PLAN_MODE_REMINDER in (result.message or "")

    @pytest.mark.asyncio
    async def test_pipeline_skips_injection_when_not_plan_mode(self) -> None:
        pipeline = MiddlewarePipeline()
        pipeline.add(PlanModeMiddleware(lambda: AgentMode.DEFAULT))
        ctx = make_context()

        result = await pipeline.run_before_turn(ctx)

        assert result.action == MiddlewareAction.CONTINUE
