from __future__ import annotations

import pytest

from vibe.core.agents.models import BUILTIN_AGENTS, AgentProfile, BuiltinAgentName
from vibe.core.config import SessionLoggingConfig, VibeConfig
from vibe.core.middleware import (
    PLAN_AGENT_REMINDER,
    ConversationContext,
    MiddlewareAction,
    MiddlewarePipeline,
    PlanAgentMiddleware,
)
from vibe.core.types import AgentStats


def make_context() -> ConversationContext:
    config = VibeConfig(session_logging=SessionLoggingConfig(enabled=False))
    return ConversationContext(messages=[], stats=AgentStats(), config=config)


class TestPlanAgentMiddleware:
    @pytest.mark.asyncio
    async def test_injects_reminder_when_plan_agent_active(self) -> None:
        middleware = PlanAgentMiddleware(lambda: BUILTIN_AGENTS[BuiltinAgentName.PLAN])
        ctx = make_context()

        result = await middleware.before_turn(ctx)

        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert result.message == PLAN_AGENT_REMINDER

    @pytest.mark.asyncio
    async def test_does_not_inject_when_default_agent(self) -> None:
        middleware = PlanAgentMiddleware(
            lambda: BUILTIN_AGENTS[BuiltinAgentName.DEFAULT]
        )
        ctx = make_context()

        result = await middleware.before_turn(ctx)

        assert result.action == MiddlewareAction.CONTINUE
        assert result.message is None

    @pytest.mark.asyncio
    async def test_does_not_inject_when_auto_approve_agent(self) -> None:
        middleware = PlanAgentMiddleware(
            lambda: BUILTIN_AGENTS[BuiltinAgentName.AUTO_APPROVE]
        )
        ctx = make_context()

        result = await middleware.before_turn(ctx)

        assert result.action == MiddlewareAction.CONTINUE
        assert result.message is None

    @pytest.mark.asyncio
    async def test_does_not_inject_when_accept_edits_agent(self) -> None:
        middleware = PlanAgentMiddleware(
            lambda: BUILTIN_AGENTS[BuiltinAgentName.ACCEPT_EDITS]
        )
        ctx = make_context()

        result = await middleware.before_turn(ctx)

        assert result.action == MiddlewareAction.CONTINUE
        assert result.message is None

    @pytest.mark.asyncio
    async def test_after_turn_always_continues(self) -> None:
        middleware = PlanAgentMiddleware(lambda: BUILTIN_AGENTS[BuiltinAgentName.PLAN])
        ctx = make_context()

        result = await middleware.after_turn(ctx)

        assert result.action == MiddlewareAction.CONTINUE

    @pytest.mark.asyncio
    async def test_dynamically_checks_agent(self) -> None:
        current_profile: AgentProfile = BUILTIN_AGENTS[BuiltinAgentName.DEFAULT]
        middleware = PlanAgentMiddleware(lambda: current_profile)
        ctx = make_context()

        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.CONTINUE

        current_profile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.INJECT_MESSAGE

        current_profile = BUILTIN_AGENTS[BuiltinAgentName.AUTO_APPROVE]
        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.CONTINUE

    @pytest.mark.asyncio
    async def test_custom_reminder(self) -> None:
        custom_reminder = "Custom plan agent reminder"
        middleware = PlanAgentMiddleware(
            lambda: BUILTIN_AGENTS[BuiltinAgentName.PLAN], reminder=custom_reminder
        )
        ctx = make_context()

        result = await middleware.before_turn(ctx)

        assert result.message == custom_reminder

    def test_reset_does_nothing(self) -> None:
        middleware = PlanAgentMiddleware(lambda: BUILTIN_AGENTS[BuiltinAgentName.PLAN])
        middleware.reset()


class TestMiddlewarePipelineWithPlanAgent:
    @pytest.mark.asyncio
    async def test_pipeline_includes_plan_agent_injection(self) -> None:
        pipeline = MiddlewarePipeline()
        pipeline.add(PlanAgentMiddleware(lambda: BUILTIN_AGENTS[BuiltinAgentName.PLAN]))
        ctx = make_context()

        result = await pipeline.run_before_turn(ctx)

        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert PLAN_AGENT_REMINDER in (result.message or "")

    @pytest.mark.asyncio
    async def test_pipeline_skips_injection_when_not_plan_agent(self) -> None:
        pipeline = MiddlewarePipeline()
        pipeline.add(
            PlanAgentMiddleware(lambda: BUILTIN_AGENTS[BuiltinAgentName.DEFAULT])
        )
        ctx = make_context()

        result = await pipeline.run_before_turn(ctx)

        assert result.action == MiddlewareAction.CONTINUE
