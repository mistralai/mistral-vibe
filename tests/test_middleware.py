from __future__ import annotations

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from vibe.core.agents.models import BUILTIN_AGENTS, CHAT, AgentProfile, BuiltinAgentName
from vibe.core.config import VibeConfig
from vibe.core.middleware import (
    CHAT_AGENT_EXIT,
    CHAT_AGENT_REMINDER,
    PLAN_AGENT_EXIT,
    PLAN_AGENT_REMINDER,
    ConversationContext,
    MiddlewareAction,
    MiddlewarePipeline,
    ReadOnlyAgentMiddleware,
    ResetReason,
)
from vibe.core.types import AgentStats, MessageList

REMINDER = "test reminder"
EXIT_MSG = "test exit"
TARGET_AGENT = BuiltinAgentName.PLAN


def _build_middleware(
    profile_getter,
    agent_name: str = TARGET_AGENT,
    reminder: str = REMINDER,
    exit_message: str = EXIT_MSG,
) -> ReadOnlyAgentMiddleware:
    return ReadOnlyAgentMiddleware(profile_getter, agent_name, reminder, exit_message)


@pytest.fixture
def ctx(vibe_config: VibeConfig) -> ConversationContext:
    return ConversationContext(
        messages=MessageList(), stats=AgentStats(), config=vibe_config
    )


class TestReadOnlyAgentMiddleware:
    @pytest.mark.asyncio
    async def test_injects_reminder_when_target_agent_active(
        self, ctx: ConversationContext
    ) -> None:
        middleware = _build_middleware(lambda: BUILTIN_AGENTS[BuiltinAgentName.PLAN])

        result = await middleware.before_turn(ctx)

        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert result.message == REMINDER

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "agent_name",
        [
            BuiltinAgentName.DEFAULT,
            BuiltinAgentName.AUTO_APPROVE,
            BuiltinAgentName.ACCEPT_EDITS,
        ],
    )
    async def test_does_not_inject_when_non_target_agent(
        self, ctx: ConversationContext, agent_name: str
    ) -> None:
        middleware = _build_middleware(lambda: BUILTIN_AGENTS[agent_name])

        result = await middleware.before_turn(ctx)

        assert result.action == MiddlewareAction.CONTINUE
        assert result.message is None

    @pytest.mark.asyncio
    async def test_injects_reminder_only_once(self, ctx: ConversationContext) -> None:
        middleware = _build_middleware(lambda: BUILTIN_AGENTS[BuiltinAgentName.PLAN])

        result1 = await middleware.before_turn(ctx)
        assert result1.action == MiddlewareAction.INJECT_MESSAGE
        assert result1.message == REMINDER

        result2 = await middleware.before_turn(ctx)
        assert result2.action == MiddlewareAction.CONTINUE
        assert result2.message is None

    @pytest.mark.asyncio
    async def test_injects_exit_message_when_leaving(
        self, ctx: ConversationContext
    ) -> None:
        current_profile: AgentProfile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
        middleware = _build_middleware(lambda: current_profile)

        await middleware.before_turn(ctx)

        current_profile = BUILTIN_AGENTS[BuiltinAgentName.DEFAULT]
        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert result.message == EXIT_MSG

    @pytest.mark.asyncio
    async def test_reinjects_reminder_on_reentry(
        self, ctx: ConversationContext
    ) -> None:
        current_profile: AgentProfile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
        middleware = _build_middleware(lambda: current_profile)

        result1 = await middleware.before_turn(ctx)
        assert result1.action == MiddlewareAction.INJECT_MESSAGE
        assert result1.message == REMINDER

        current_profile = BUILTIN_AGENTS[BuiltinAgentName.DEFAULT]
        result2 = await middleware.before_turn(ctx)
        assert result2.action == MiddlewareAction.INJECT_MESSAGE
        assert result2.message == EXIT_MSG

        current_profile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
        result3 = await middleware.before_turn(ctx)
        assert result3.action == MiddlewareAction.INJECT_MESSAGE
        assert result3.message == REMINDER

    @pytest.mark.asyncio
    async def test_custom_reminder(self, ctx: ConversationContext) -> None:
        custom_reminder = "Custom reminder"
        middleware = _build_middleware(
            lambda: BUILTIN_AGENTS[BuiltinAgentName.PLAN], reminder=custom_reminder
        )

        result = await middleware.before_turn(ctx)

        assert result.message == custom_reminder

    @pytest.mark.asyncio
    async def test_custom_exit_message(self, ctx: ConversationContext) -> None:
        custom_exit = "Custom exit message"
        current_profile: AgentProfile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
        middleware = _build_middleware(
            lambda: current_profile, exit_message=custom_exit
        )

        await middleware.before_turn(ctx)

        current_profile = BUILTIN_AGENTS[BuiltinAgentName.DEFAULT]
        result = await middleware.before_turn(ctx)
        assert result.message == custom_exit

    @pytest.mark.asyncio
    async def test_reset_clears_state(self, ctx: ConversationContext) -> None:
        middleware = _build_middleware(lambda: BUILTIN_AGENTS[BuiltinAgentName.PLAN])
        await middleware.before_turn(ctx)

        middleware.reset()

        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.INJECT_MESSAGE

    @pytest.mark.asyncio
    async def test_exit_message_fires_only_once(self, ctx: ConversationContext) -> None:
        current_profile: AgentProfile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
        middleware = _build_middleware(lambda: current_profile)

        await middleware.before_turn(ctx)

        current_profile = BUILTIN_AGENTS[BuiltinAgentName.DEFAULT]
        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert result.message == EXIT_MSG

        result2 = await middleware.before_turn(ctx)
        assert result2.action == MiddlewareAction.CONTINUE
        assert result2.message is None

    @pytest.mark.asyncio
    async def test_multiple_turns_after_entry(self, ctx: ConversationContext) -> None:
        middleware = _build_middleware(lambda: BUILTIN_AGENTS[BuiltinAgentName.PLAN])

        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.INJECT_MESSAGE

        for _ in range(5):
            result = await middleware.before_turn(ctx)
            assert result.action == MiddlewareAction.CONTINUE
            assert result.message is None

    @pytest.mark.asyncio
    async def test_multiple_turns_after_exit(self, ctx: ConversationContext) -> None:
        current_profile: AgentProfile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
        middleware = _build_middleware(lambda: current_profile)

        await middleware.before_turn(ctx)

        current_profile = BUILTIN_AGENTS[BuiltinAgentName.DEFAULT]
        await middleware.before_turn(ctx)

        for _ in range(5):
            result = await middleware.before_turn(ctx)
            assert result.action == MiddlewareAction.CONTINUE
            assert result.message is None

    @pytest.mark.asyncio
    async def test_rapid_toggling_multiple_cycles(
        self, ctx: ConversationContext
    ) -> None:
        current_profile: AgentProfile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
        middleware = _build_middleware(lambda: current_profile)

        for _ in range(3):
            current_profile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
            result = await middleware.before_turn(ctx)
            assert result.action == MiddlewareAction.INJECT_MESSAGE
            assert result.message == REMINDER

            current_profile = BUILTIN_AGENTS[BuiltinAgentName.DEFAULT]
            result = await middleware.before_turn(ctx)
            assert result.action == MiddlewareAction.INJECT_MESSAGE
            assert result.message == EXIT_MSG

    @pytest.mark.asyncio
    async def test_exit_to_non_default_agent(self, ctx: ConversationContext) -> None:
        current_profile: AgentProfile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
        middleware = _build_middleware(lambda: current_profile)

        await middleware.before_turn(ctx)

        current_profile = BUILTIN_AGENTS[BuiltinAgentName.AUTO_APPROVE]
        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert result.message == EXIT_MSG

    @pytest.mark.asyncio
    async def test_switching_between_non_target_agents(
        self, ctx: ConversationContext
    ) -> None:
        current_profile: AgentProfile = BUILTIN_AGENTS[BuiltinAgentName.DEFAULT]
        middleware = _build_middleware(lambda: current_profile)

        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.CONTINUE

        current_profile = BUILTIN_AGENTS[BuiltinAgentName.AUTO_APPROVE]
        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.CONTINUE

        current_profile = BUILTIN_AGENTS[BuiltinAgentName.ACCEPT_EDITS]
        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.CONTINUE

    @pytest.mark.asyncio
    async def test_non_target_to_target_entry(self, ctx: ConversationContext) -> None:
        """Starting in a non-target agent then entering target should inject reminder."""
        current_profile: AgentProfile = BUILTIN_AGENTS[BuiltinAgentName.AUTO_APPROVE]
        middleware = _build_middleware(lambda: current_profile)

        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.CONTINUE

        current_profile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert result.message == REMINDER

    @pytest.mark.asyncio
    async def test_reset_while_inactive_after_exit(
        self, ctx: ConversationContext
    ) -> None:
        current_profile: AgentProfile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
        middleware = _build_middleware(lambda: current_profile)

        await middleware.before_turn(ctx)
        current_profile = BUILTIN_AGENTS[BuiltinAgentName.DEFAULT]
        await middleware.before_turn(ctx)

        middleware.reset()

        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.CONTINUE

    @pytest.mark.asyncio
    async def test_reset_while_inactive_then_reenter(
        self, ctx: ConversationContext
    ) -> None:
        current_profile: AgentProfile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
        middleware = _build_middleware(lambda: current_profile)

        await middleware.before_turn(ctx)
        current_profile = BUILTIN_AGENTS[BuiltinAgentName.DEFAULT]
        await middleware.before_turn(ctx)

        middleware.reset()

        current_profile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert result.message == REMINDER

    @pytest.mark.asyncio
    async def test_reset_with_compact_reason(self, ctx: ConversationContext) -> None:
        middleware = _build_middleware(lambda: BUILTIN_AGENTS[BuiltinAgentName.PLAN])
        await middleware.before_turn(ctx)

        middleware.reset(ResetReason.COMPACT)

        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert result.message == REMINDER

    @pytest.mark.asyncio
    async def test_entry_then_continuation_then_exit_then_continuation(
        self, ctx: ConversationContext
    ) -> None:
        """Each call sees one transition at a time."""
        current_profile: AgentProfile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
        middleware = _build_middleware(lambda: current_profile)

        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert result.message == REMINDER

        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.CONTINUE

        current_profile = BUILTIN_AGENTS[BuiltinAgentName.DEFAULT]
        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert result.message == EXIT_MSG

        result = await middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.CONTINUE


class TestMiddlewarePipelineWithReadOnlyAgent:
    @pytest.mark.asyncio
    async def test_pipeline_includes_injection(self, ctx: ConversationContext) -> None:
        pipeline = MiddlewarePipeline()
        pipeline.add(
            ReadOnlyAgentMiddleware(
                lambda: BUILTIN_AGENTS[BuiltinAgentName.PLAN],
                BuiltinAgentName.PLAN,
                PLAN_AGENT_REMINDER,
                PLAN_AGENT_EXIT,
            )
        )

        result = await pipeline.run_before_turn(ctx)

        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert PLAN_AGENT_REMINDER in (result.message or "")

    @pytest.mark.asyncio
    async def test_pipeline_skips_injection_when_not_target_agent(
        self, ctx: ConversationContext
    ) -> None:
        pipeline = MiddlewarePipeline()
        pipeline.add(
            ReadOnlyAgentMiddleware(
                lambda: BUILTIN_AGENTS[BuiltinAgentName.DEFAULT],
                BuiltinAgentName.PLAN,
                PLAN_AGENT_REMINDER,
                PLAN_AGENT_EXIT,
            )
        )

        result = await pipeline.run_before_turn(ctx)

        assert result.action == MiddlewareAction.CONTINUE

    @pytest.mark.asyncio
    async def test_direct_plan_to_chat_transition_delivers_both_messages(
        self, ctx: ConversationContext
    ) -> None:
        current_profile: AgentProfile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
        pipeline = MiddlewarePipeline()
        pipeline.add(
            ReadOnlyAgentMiddleware(
                lambda: current_profile,
                BuiltinAgentName.PLAN,
                PLAN_AGENT_REMINDER,
                PLAN_AGENT_EXIT,
            )
        )
        pipeline.add(
            ReadOnlyAgentMiddleware(
                lambda: current_profile,
                BuiltinAgentName.CHAT,
                CHAT_AGENT_REMINDER,
                CHAT_AGENT_EXIT,
            )
        )

        result = await pipeline.run_before_turn(ctx)
        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert PLAN_AGENT_REMINDER in (result.message or "")

        current_profile = CHAT
        result = await pipeline.run_before_turn(ctx)
        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert PLAN_AGENT_EXIT in (result.message or "")
        assert CHAT_AGENT_REMINDER in (result.message or "")

        current_profile = BUILTIN_AGENTS[BuiltinAgentName.PLAN]
        result = await pipeline.run_before_turn(ctx)
        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert CHAT_AGENT_EXIT in (result.message or "")
        assert PLAN_AGENT_REMINDER in (result.message or "")


def _find_plan_middleware(agent) -> ReadOnlyAgentMiddleware:
    return next(
        mw
        for mw in agent.middleware_pipeline.middlewares
        if isinstance(mw, ReadOnlyAgentMiddleware)
        and mw._agent_name == BuiltinAgentName.PLAN
    )


class TestReadOnlyAgentMiddlewareIntegration:
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

        plan_middleware = _find_plan_middleware(agent)

        ctx = ConversationContext(
            messages=agent.messages, stats=agent.stats, config=agent.config
        )
        result = await plan_middleware.before_turn(ctx)
        assert result.action == MiddlewareAction.INJECT_MESSAGE
        assert result.message == PLAN_AGENT_REMINDER

        await agent.switch_agent(BuiltinAgentName.DEFAULT)

        plan_middleware_after = _find_plan_middleware(agent)
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

        plan_middleware = _find_plan_middleware(agent)

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

        plan_middleware = _find_plan_middleware(agent)

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

        plan_middleware = _find_plan_middleware(agent)

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

        plan_middleware = _find_plan_middleware(agent)

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
