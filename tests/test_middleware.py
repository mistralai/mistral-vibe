from __future__ import annotations

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from vibe.core.agents.models import BUILTIN_AGENTS, AgentProfile, BuiltinAgentName
from vibe.core.config import VibeConfig
from vibe.core.middleware import (
    PLAN_AGENT_EXIT,
    PLAN_AGENT_REMINDER,
    ConversationContext,
    MiddlewareAction,
    MiddlewarePipeline,
    PlanAgentMiddleware,
    ResetReason,
)
from vibe.core.types import AgentStats


@pytest.fixture
def ctx(vibe_config: VibeConfig) -> ConversationContext:
    return ConversationContext(messages=[], stats=AgentStats(), config=vibe_config)


class TestPlanAgentMiddleware:
    @pytest.mark.asyncio
    async def test_injects_reminder_when_plan_agent_active(
        self, ctx: ConversationContext
    ) -> None:
        middleware = PlanAgentMiddleware(lambda: BUILTIN_AGENTS[BuiltinAgentName.PLAN])

        result = await middleware.before_turn(ctx)

        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert result.message == PLAN_AGENT_REMINDER

    @pytest.mark.asyncio
    async def test_does_not_inject_when_default_agent(
        self, ctx: ConversationContext
    ) -> None:
        middleware = PlanAgentMiddleware(
            lambda: BUILTIN_AGENTS[BuiltinAgentName.DEFAULT]
        )

        result = await middleware.before_turn(ctx)

        assert result.action == MiddlewareAction.CONTINUE
        assert result.message is None

    @pytest.mark.asyncio
    async def test_does_not_inject_when_auto_approve_agent(
        self, ctx: ConversationContext
    ) -> None:
        middleware = PlanAgentMiddleware(
            lambda: BUILTIN_AGENTS[BuiltinAgentName.AUTO_APPROVE]
        )

        result = await middleware.before_turn(ctx)

        assert result.action == MiddlewareAction.CONTINUE
        assert result.message is None

    @pytest.mark.asyncio
    async def test_does_not_inject_when_accept_edits_agent(
        self, ctx: ConversationContext
    ) -> None:
        middleware = PlanAgentMiddleware(
            lambda: BUILTIN_AGENTS[BuiltinAgentName.ACCEPT_EDITS]
        )

        result = await middleware.before_turn(ctx)

        assert result.action == MiddlewareAction.CONTINUE
        assert result.message is None

    @pytest.mark.asyncio
    async def test_injects_reminder_only_once_while_in_plan_mode(
        self, ctx: ConversationContext
    ) -> None:
        middleware = PlanAgentMiddleware(lambda: BUILTIN_AGENTS[BuiltinAgentName.PLAN])

        result1 = await middleware.before_turn(ctx)
        assert result1.action == MiddlewareAction.INJECT_MESSAGE
        assert result1.message == PLAN_AGENT_REMINDER

        result2 = await middleware.before_turn(ctx)
        assert result2.action == MiddlewareAction.CONTINUE
        assert result2.message is None

    @pytest.mark.asyncio
    async def test_injects_exit_message_when_leaving_plan_mode(
        self, ctx: ConversationContext
    ) -> None:
        current_profile: AgentProfile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
        middleware = PlanAgentMiddleware(lambda: current_profile)

        # Enter plan mode
        await middleware.before_turn(ctx)

        # Leave plan mode
        current_profile = BUILTIN_AGENTS[BuiltinAgentName.DEFAULT]
        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert result.message == PLAN_AGENT_EXIT

    @pytest.mark.asyncio
    async def test_reinjects_reminder_when_reentering_plan_mode(
        self, ctx: ConversationContext
    ) -> None:
        current_profile: AgentProfile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
        middleware = PlanAgentMiddleware(lambda: current_profile)

        # Enter plan mode - should inject reminder
        result1 = await middleware.before_turn(ctx)
        assert result1.action == MiddlewareAction.INJECT_MESSAGE
        assert result1.message == PLAN_AGENT_REMINDER

        # Leave plan mode - should inject exit message
        current_profile = BUILTIN_AGENTS[BuiltinAgentName.DEFAULT]
        result2 = await middleware.before_turn(ctx)
        assert result2.action == MiddlewareAction.INJECT_MESSAGE
        assert result2.message == PLAN_AGENT_EXIT

        # Re-enter plan mode - should inject reminder again
        current_profile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
        result3 = await middleware.before_turn(ctx)
        assert result3.action == MiddlewareAction.INJECT_MESSAGE
        assert result3.message == PLAN_AGENT_REMINDER

    @pytest.mark.asyncio
    async def test_custom_reminder(self, ctx: ConversationContext) -> None:
        custom_reminder = "Custom plan agent reminder"
        middleware = PlanAgentMiddleware(
            lambda: BUILTIN_AGENTS[BuiltinAgentName.PLAN], reminder=custom_reminder
        )

        result = await middleware.before_turn(ctx)

        assert result.message == custom_reminder

    @pytest.mark.asyncio
    async def test_reset_clears_state(self, ctx: ConversationContext) -> None:
        middleware = PlanAgentMiddleware(lambda: BUILTIN_AGENTS[BuiltinAgentName.PLAN])
        await middleware.before_turn(ctx)  # Enter and inject

        middleware.reset()

        # Should inject again after reset
        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.INJECT_MESSAGE

    @pytest.mark.asyncio
    async def test_exit_message_fires_only_once(self, ctx: ConversationContext) -> None:
        current_profile: AgentProfile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
        middleware = PlanAgentMiddleware(lambda: current_profile)

        # Enter plan mode
        await middleware.before_turn(ctx)

        # Leave plan mode - first call should inject exit
        current_profile = BUILTIN_AGENTS[BuiltinAgentName.DEFAULT]
        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert result.message == PLAN_AGENT_EXIT

        # Subsequent calls in default mode should be CONTINUE
        result2 = await middleware.before_turn(ctx)
        assert result2.action == MiddlewareAction.CONTINUE
        assert result2.message is None

    @pytest.mark.asyncio
    async def test_multiple_turns_in_plan_mode_after_entry(
        self, ctx: ConversationContext
    ) -> None:
        middleware = PlanAgentMiddleware(lambda: BUILTIN_AGENTS[BuiltinAgentName.PLAN])

        # First turn: inject reminder
        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.INJECT_MESSAGE

        # Several more turns in plan mode: all should be CONTINUE
        for _ in range(5):
            result = await middleware.before_turn(ctx)
            assert result.action == MiddlewareAction.CONTINUE
            assert result.message is None

    @pytest.mark.asyncio
    async def test_multiple_turns_in_default_mode_after_exit(
        self, ctx: ConversationContext
    ) -> None:
        current_profile: AgentProfile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
        middleware = PlanAgentMiddleware(lambda: current_profile)

        await middleware.before_turn(ctx)  # enter plan

        current_profile = BUILTIN_AGENTS[BuiltinAgentName.DEFAULT]
        await middleware.before_turn(ctx)  # exit plan (fires exit message)

        # Several more turns in default mode: all should be CONTINUE
        for _ in range(5):
            result = await middleware.before_turn(ctx)
            assert result.action == MiddlewareAction.CONTINUE
            assert result.message is None

    @pytest.mark.asyncio
    async def test_rapid_toggling_plan_default_multiple_cycles(
        self, ctx: ConversationContext
    ) -> None:
        current_profile: AgentProfile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
        middleware = PlanAgentMiddleware(lambda: current_profile)

        for _ in range(3):
            # Enter plan
            current_profile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
            result = await middleware.before_turn(ctx)
            assert result.action == MiddlewareAction.INJECT_MESSAGE
            assert result.message == PLAN_AGENT_REMINDER

            # Leave plan
            current_profile = BUILTIN_AGENTS[BuiltinAgentName.DEFAULT]
            result = await middleware.before_turn(ctx)
            assert result.action == MiddlewareAction.INJECT_MESSAGE
            assert result.message == PLAN_AGENT_EXIT

    @pytest.mark.asyncio
    async def test_exit_to_non_default_agent(self, ctx: ConversationContext) -> None:
        current_profile: AgentProfile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
        middleware = PlanAgentMiddleware(lambda: current_profile)

        await middleware.before_turn(ctx)  # enter plan

        # Switch to auto_approve (not default)
        current_profile = BUILTIN_AGENTS[BuiltinAgentName.AUTO_APPROVE]
        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert result.message == PLAN_AGENT_EXIT

    @pytest.mark.asyncio
    async def test_exit_to_accept_edits_agent(self, ctx: ConversationContext) -> None:
        current_profile: AgentProfile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
        middleware = PlanAgentMiddleware(lambda: current_profile)

        await middleware.before_turn(ctx)  # enter plan

        # Switch to accept_edits
        current_profile = BUILTIN_AGENTS[BuiltinAgentName.ACCEPT_EDITS]
        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert result.message == PLAN_AGENT_EXIT

    @pytest.mark.asyncio
    async def test_switching_between_non_plan_agents(
        self, ctx: ConversationContext
    ) -> None:
        current_profile: AgentProfile = BUILTIN_AGENTS[BuiltinAgentName.DEFAULT]
        middleware = PlanAgentMiddleware(lambda: current_profile)

        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.CONTINUE

        current_profile = BUILTIN_AGENTS[BuiltinAgentName.AUTO_APPROVE]
        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.CONTINUE

        current_profile = BUILTIN_AGENTS[BuiltinAgentName.ACCEPT_EDITS]
        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.CONTINUE

    @pytest.mark.asyncio
    async def test_non_plan_to_plan_entry(self, ctx: ConversationContext) -> None:
        """Starting in a non-plan agent then entering plan should inject reminder."""
        current_profile: AgentProfile = BUILTIN_AGENTS[BuiltinAgentName.AUTO_APPROVE]
        middleware = PlanAgentMiddleware(lambda: current_profile)

        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.CONTINUE

        # Now switch to plan
        current_profile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert result.message == PLAN_AGENT_REMINDER

    @pytest.mark.asyncio
    async def test_reset_while_in_default_after_exiting_plan(
        self, ctx: ConversationContext
    ) -> None:
        current_profile: AgentProfile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
        middleware = PlanAgentMiddleware(lambda: current_profile)

        await middleware.before_turn(ctx)  # enter plan
        current_profile = BUILTIN_AGENTS[BuiltinAgentName.DEFAULT]
        await middleware.before_turn(ctx)  # exit plan

        middleware.reset()

        # Still in default mode - should CONTINUE (no phantom exit message)
        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.CONTINUE

    @pytest.mark.asyncio
    async def test_reset_while_in_default_then_reenter_plan(
        self, ctx: ConversationContext
    ) -> None:
        current_profile: AgentProfile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
        middleware = PlanAgentMiddleware(lambda: current_profile)

        await middleware.before_turn(ctx)  # enter plan
        current_profile = BUILTIN_AGENTS[BuiltinAgentName.DEFAULT]
        await middleware.before_turn(ctx)  # exit plan

        middleware.reset()

        # Re-enter plan after reset
        current_profile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert result.message == PLAN_AGENT_REMINDER

    @pytest.mark.asyncio
    async def test_reset_with_compact_reason(self, ctx: ConversationContext) -> None:
        middleware = PlanAgentMiddleware(lambda: BUILTIN_AGENTS[BuiltinAgentName.PLAN])
        await middleware.before_turn(ctx)  # enter and inject

        middleware.reset(ResetReason.COMPACT)

        # Should reinject reminder after compact reset
        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert result.message == PLAN_AGENT_REMINDER

    @pytest.mark.asyncio
    async def test_custom_exit_message(self, ctx: ConversationContext) -> None:
        custom_exit = "Custom exit message"
        current_profile: AgentProfile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
        middleware = PlanAgentMiddleware(
            lambda: current_profile, exit_message=custom_exit
        )

        await middleware.before_turn(ctx)  # enter plan

        current_profile = BUILTIN_AGENTS[BuiltinAgentName.DEFAULT]
        result = await middleware.before_turn(ctx)
        assert result.message == custom_exit

    @pytest.mark.asyncio
    async def test_plan_entry_then_immediate_exit_same_not_possible(
        self, ctx: ConversationContext
    ) -> None:
        """Even if profile changes between two calls, each call sees one transition."""
        current_profile: AgentProfile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
        middleware = PlanAgentMiddleware(lambda: current_profile)

        # First call: entry
        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert result.message == PLAN_AGENT_REMINDER

        # Second call (still plan): no injection
        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.CONTINUE

        # Third call (switched to default): exit
        current_profile = BUILTIN_AGENTS[BuiltinAgentName.DEFAULT]
        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert result.message == PLAN_AGENT_EXIT

        # Fourth call (still default): no injection
        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.CONTINUE


class TestMiddlewarePipelineWithPlanAgent:
    @pytest.mark.asyncio
    async def test_pipeline_includes_plan_agent_injection(
        self, ctx: ConversationContext
    ) -> None:
        pipeline = MiddlewarePipeline()
        pipeline.add(PlanAgentMiddleware(lambda: BUILTIN_AGENTS[BuiltinAgentName.PLAN]))

        result = await pipeline.run_before_turn(ctx)

        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert PLAN_AGENT_REMINDER in (result.message or "")

    @pytest.mark.asyncio
    async def test_pipeline_skips_injection_when_not_plan_agent(
        self, ctx: ConversationContext
    ) -> None:
        pipeline = MiddlewarePipeline()
        pipeline.add(
            PlanAgentMiddleware(lambda: BUILTIN_AGENTS[BuiltinAgentName.DEFAULT])
        )

        result = await pipeline.run_before_turn(ctx)

        assert result.action == MiddlewareAction.CONTINUE


class TestPlanAgentMiddlewareIntegration:
    @pytest.mark.asyncio
    async def test_switch_agent_preserves_middleware_state_for_exit_message(
        self,
    ) -> None:
        config = build_test_vibe_config(
            auto_compact_threshold=0,
            system_prompt_id="tests",
            include_project_context=False,
            include_prompt_detail=False,
            include_model_info=False,
            include_commit_signature=False,
            enabled_tools=[],
        )
        agent = build_test_agent_loop(config=config, agent_name=BuiltinAgentName.PLAN)

        plan_middleware = next(
            mw
            for mw in agent.middleware_pipeline.middlewares
            if isinstance(mw, PlanAgentMiddleware)
        )

        ctx = ConversationContext(
            messages=agent.messages, stats=agent.stats, config=agent.config
        )
        result = await plan_middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert result.message == PLAN_AGENT_REMINDER

        await agent.switch_agent(BuiltinAgentName.DEFAULT)

        plan_middleware_after = next(
            mw
            for mw in agent.middleware_pipeline.middlewares
            if isinstance(mw, PlanAgentMiddleware)
        )
        assert plan_middleware is plan_middleware_after

        ctx = ConversationContext(
            messages=agent.messages, stats=agent.stats, config=agent.config
        )
        result = await plan_middleware_after.before_turn(ctx)
        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert result.message == PLAN_AGENT_EXIT

    @pytest.mark.asyncio
    async def test_switch_agent_allows_reinjection_on_reentry(self) -> None:
        config = build_test_vibe_config(
            auto_compact_threshold=0,
            system_prompt_id="tests",
            include_project_context=False,
            include_prompt_detail=False,
            include_model_info=False,
            include_commit_signature=False,
            enabled_tools=[],
        )
        agent = build_test_agent_loop(config=config, agent_name=BuiltinAgentName.PLAN)

        plan_middleware = next(
            mw
            for mw in agent.middleware_pipeline.middlewares
            if isinstance(mw, PlanAgentMiddleware)
        )

        ctx = ConversationContext(
            messages=agent.messages, stats=agent.stats, config=agent.config
        )
        await plan_middleware.before_turn(ctx)

        await agent.switch_agent(BuiltinAgentName.DEFAULT)

        ctx = ConversationContext(
            messages=agent.messages, stats=agent.stats, config=agent.config
        )
        result = await plan_middleware.before_turn(ctx)
        assert result.message == PLAN_AGENT_EXIT

        await agent.switch_agent(BuiltinAgentName.PLAN)

        ctx = ConversationContext(
            messages=agent.messages, stats=agent.stats, config=agent.config
        )
        result = await plan_middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert result.message == PLAN_AGENT_REMINDER

    @pytest.mark.asyncio
    async def test_switch_plan_to_auto_approve_fires_exit(self) -> None:
        config = build_test_vibe_config(
            auto_compact_threshold=0,
            system_prompt_id="tests",
            include_project_context=False,
            include_prompt_detail=False,
            include_model_info=False,
            include_commit_signature=False,
            enabled_tools=[],
        )
        agent = build_test_agent_loop(config=config, agent_name=BuiltinAgentName.PLAN)

        plan_middleware = next(
            mw
            for mw in agent.middleware_pipeline.middlewares
            if isinstance(mw, PlanAgentMiddleware)
        )

        ctx = ConversationContext(
            messages=agent.messages, stats=agent.stats, config=agent.config
        )
        await plan_middleware.before_turn(ctx)  # enter plan

        await agent.switch_agent(BuiltinAgentName.AUTO_APPROVE)

        ctx = ConversationContext(
            messages=agent.messages, stats=agent.stats, config=agent.config
        )
        result = await plan_middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert result.message == PLAN_AGENT_EXIT

    @pytest.mark.asyncio
    async def test_switch_between_non_plan_agents_no_injection(self) -> None:
        config = build_test_vibe_config(
            auto_compact_threshold=0,
            system_prompt_id="tests",
            include_project_context=False,
            include_prompt_detail=False,
            include_model_info=False,
            include_commit_signature=False,
            enabled_tools=[],
        )
        agent = build_test_agent_loop(
            config=config, agent_name=BuiltinAgentName.DEFAULT
        )

        plan_middleware = next(
            mw
            for mw in agent.middleware_pipeline.middlewares
            if isinstance(mw, PlanAgentMiddleware)
        )

        ctx = ConversationContext(
            messages=agent.messages, stats=agent.stats, config=agent.config
        )
        result = await plan_middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.CONTINUE

        await agent.switch_agent(BuiltinAgentName.AUTO_APPROVE)

        ctx = ConversationContext(
            messages=agent.messages, stats=agent.stats, config=agent.config
        )
        result = await plan_middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.CONTINUE

    @pytest.mark.asyncio
    async def test_full_lifecycle_plan_default_plan_default(self) -> None:
        """Integration test for a full plan -> default -> plan -> default cycle."""
        config = build_test_vibe_config(
            auto_compact_threshold=0,
            system_prompt_id="tests",
            include_project_context=False,
            include_prompt_detail=False,
            include_model_info=False,
            include_commit_signature=False,
            enabled_tools=[],
        )
        agent = build_test_agent_loop(config=config, agent_name=BuiltinAgentName.PLAN)

        plan_middleware = next(
            mw
            for mw in agent.middleware_pipeline.middlewares
            if isinstance(mw, PlanAgentMiddleware)
        )

        def _ctx():
            return ConversationContext(
                messages=agent.messages, stats=agent.stats, config=agent.config
            )

        # 1. Enter plan: inject reminder
        r = await plan_middleware.before_turn(_ctx())
        assert r.action == MiddlewareAction.INJECT_MESSAGE
        assert r.message == PLAN_AGENT_REMINDER

        # 2. Stay in plan: no injection
        r = await plan_middleware.before_turn(_ctx())
        assert r.action == MiddlewareAction.CONTINUE

        # 3. Switch to default: inject exit
        await agent.switch_agent(BuiltinAgentName.DEFAULT)
        r = await plan_middleware.before_turn(_ctx())
        assert r.action == MiddlewareAction.INJECT_MESSAGE
        assert r.message == PLAN_AGENT_EXIT

        # 4. Stay in default: no injection
        r = await plan_middleware.before_turn(_ctx())
        assert r.action == MiddlewareAction.CONTINUE

        # 5. Switch back to plan: inject reminder again
        await agent.switch_agent(BuiltinAgentName.PLAN)
        r = await plan_middleware.before_turn(_ctx())
        assert r.action == MiddlewareAction.INJECT_MESSAGE
        assert r.message == PLAN_AGENT_REMINDER

        # 6. Stay in plan: no injection
        r = await plan_middleware.before_turn(_ctx())
        assert r.action == MiddlewareAction.CONTINUE

        # 7. Switch to default again: inject exit
        await agent.switch_agent(BuiltinAgentName.DEFAULT)
        r = await plan_middleware.before_turn(_ctx())
        assert r.action == MiddlewareAction.INJECT_MESSAGE
        assert r.message == PLAN_AGENT_EXIT

        # 8. Stay in default: no injection
        r = await plan_middleware.before_turn(_ctx())
        assert r.action == MiddlewareAction.CONTINUE
