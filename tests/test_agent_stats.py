from __future__ import annotations

from collections.abc import Callable

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import (
    Backend,
    ModelConfig,
    ProviderConfig,
    SessionLoggingConfig,
    VibeConfig,
)
from vibe.core.tools.base import BaseToolConfig, ToolPermission
from vibe.core.types import (
    AgentStats,
    AssistantEvent,
    CompactEndEvent,
    CompactStartEvent,
    FunctionCall,
    LLMMessage,
    Role,
    ToolCall,
    UserMessageEvent,
)


def make_config(
    *,
    system_prompt_id: str = "tests",
    active_model: str = "devstral-latest",
    input_price: float = 0.4,
    output_price: float = 2.0,
    disable_logging: bool = True,
    auto_compact_threshold: int = 0,
    include_project_context: bool = False,
    include_prompt_detail: bool = False,
    enabled_tools: list[str] | None = None,
    todo_permission: ToolPermission = ToolPermission.ALWAYS,
) -> VibeConfig:
    models = [
        ModelConfig(
            name="mistral-vibe-cli-latest",
            provider="mistral",
            alias="devstral-latest",
            input_price=input_price,
            output_price=output_price,
        ),
        ModelConfig(
            name="devstral-small-latest",
            provider="mistral",
            alias="devstral-small",
            input_price=0.1,
            output_price=0.3,
        ),
        ModelConfig(
            name="strawberry",
            provider="lechat",
            alias="strawberry",
            input_price=2.5,
            output_price=10.0,
        ),
    ]
    providers = [
        ProviderConfig(
            name="mistral",
            api_base="https://api.mistral.ai/v1",
            api_key_env_var="MISTRAL_API_KEY",
            backend=Backend.MISTRAL,
        ),
        ProviderConfig(
            name="lechat",
            api_base="https://api.mistral.ai/v1",
            api_key_env_var="LECHAT_API_KEY",
            backend=Backend.MISTRAL,
        ),
    ]
    return build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=not disable_logging),
        auto_compact_threshold=auto_compact_threshold,
        system_prompt_id=system_prompt_id,
        include_project_context=include_project_context,
        include_prompt_detail=include_prompt_detail,
        active_model=active_model,
        models=models,
        providers=providers,
        enabled_tools=enabled_tools or [],
        tools={"todo": BaseToolConfig(permission=todo_permission)},
    )


@pytest.fixture
def observer_capture() -> tuple[list[LLMMessage], Callable[[LLMMessage], None]]:
    observed: list[LLMMessage] = []

    def observer(msg: LLMMessage) -> None:
        observed.append(msg)

    return observed, observer


class TestAgentStatsHelpers:
    def test_update_pricing(self) -> None:
        stats = AgentStats()
        stats.update_pricing(1.5, 3.0)
        assert stats.input_price_per_million == 1.5
        assert stats.output_price_per_million == 3.0

    def test_reset_context_state_preserves_cumulative(self) -> None:
        stats = AgentStats(
            steps=5,
            session_prompt_tokens=1000,
            session_completion_tokens=500,
            tool_calls_succeeded=3,
            tool_calls_failed=1,
            context_tokens=800,
            last_turn_prompt_tokens=100,
            last_turn_completion_tokens=50,
            last_turn_duration=1.5,
            tokens_per_second=33.3,
            input_price_per_million=0.4,
            output_price_per_million=2.0,
        )

        stats.reset_context_state()

        assert stats.steps == 5
        assert stats.session_prompt_tokens == 1000
        assert stats.session_completion_tokens == 500
        assert stats.tool_calls_succeeded == 3
        assert stats.tool_calls_failed == 1
        assert stats.input_price_per_million == 0.4
        assert stats.output_price_per_million == 2.0

        assert stats.context_tokens == 0
        assert stats.last_turn_prompt_tokens == 0
        assert stats.last_turn_completion_tokens == 0
        assert stats.last_turn_duration == 0.0
        assert stats.tokens_per_second == 0.0

    def test_session_cost_computed_from_current_pricing(self) -> None:
        stats = AgentStats(
            session_prompt_tokens=1_000_000,
            session_completion_tokens=500_000,
            input_price_per_million=1.0,
            output_price_per_million=2.0,
        )
        # Cost = 1M * $1/M + 0.5M * $2/M = $1 + $1 = $2
        assert stats.session_cost == 2.0

        stats.update_pricing(2.0, 4.0)
        # Cost = 1M * $2/M + 0.5M * $4/M = $2 + $2 = $4
        assert stats.session_cost == 4.0


class TestReloadPreservesStats:
    @pytest.mark.asyncio
    async def test_reload_preserves_session_tokens(self) -> None:
        backend = FakeBackend(mock_llm_chunk(content="First response"))
        agent = build_test_agent_loop(config=make_config(), backend=backend)

        async for _ in agent.act("Hello"):
            pass

        old_session_prompt = agent.stats.session_prompt_tokens
        old_session_completion = agent.stats.session_completion_tokens
        assert old_session_prompt > 0
        assert old_session_completion > 0

        await agent.reload_with_initial_messages()

        assert agent.stats.session_prompt_tokens == old_session_prompt
        assert agent.stats.session_completion_tokens == old_session_completion

    @pytest.mark.asyncio
    async def test_reload_preserves_tool_call_stats(self) -> None:
        backend = FakeBackend([
            mock_llm_chunk(
                content="Calling tool",
                tool_calls=[
                    ToolCall(
                        id="tc1",
                        index=0,
                        function=FunctionCall(
                            name="todo", arguments='{"action": "read"}'
                        ),
                    )
                ],
            ),
            mock_llm_chunk(content="Done"),
        ])
        config = make_config(enabled_tools=["todo"])
        agent = build_test_agent_loop(
            config=config, agent_name=BuiltinAgentName.AUTO_APPROVE, backend=backend
        )

        async for _ in agent.act("Check todos"):
            pass

        assert agent.stats.tool_calls_succeeded == 1
        assert agent.stats.tool_calls_agreed == 1

        await agent.reload_with_initial_messages()

        assert agent.stats.tool_calls_succeeded == 1
        assert agent.stats.tool_calls_agreed == 1

    @pytest.mark.asyncio
    async def test_reload_preserves_steps(self) -> None:
        backend = FakeBackend([
            [mock_llm_chunk(content="R1")],
            [mock_llm_chunk(content="R2")],
        ])
        agent = build_test_agent_loop(config=make_config(), backend=backend)

        async for _ in agent.act("First"):
            pass
        async for _ in agent.act("Second"):
            pass

        old_steps = agent.stats.steps
        assert old_steps >= 2

        await agent.reload_with_initial_messages()

        assert agent.stats.steps == old_steps

    @pytest.mark.asyncio
    async def test_reload_preserves_context_tokens_when_messages_preserved(
        self,
    ) -> None:
        backend = FakeBackend(mock_llm_chunk(content="Response"))
        agent = build_test_agent_loop(config=make_config(), backend=backend)
        [_ async for _ in agent.act("Hello")]
        assert agent.stats.context_tokens > 0
        initial_context_tokens = agent.stats.context_tokens
        assert len(agent.messages) > 1

        await agent.reload_with_initial_messages()

        assert len(agent.messages) > 1
        assert agent.stats.context_tokens == initial_context_tokens

    @pytest.mark.asyncio
    async def test_reload_resets_context_tokens_when_no_messages(self) -> None:
        backend = FakeBackend([])
        agent = build_test_agent_loop(config=make_config(), backend=backend)
        assert len(agent.messages) == 1
        assert agent.stats.context_tokens == 0

        await agent.reload_with_initial_messages()

        assert len(agent.messages) == 1
        assert agent.stats.context_tokens == 0

    @pytest.mark.asyncio
    async def test_reload_resets_context_tokens_when_system_prompt_changes(
        self,
    ) -> None:
        backend = FakeBackend(mock_llm_chunk(content="Response"))
        config1 = make_config(system_prompt_id="tests")
        config2 = make_config(system_prompt_id="cli")
        agent = build_test_agent_loop(config=config1, backend=backend)
        [_ async for _ in agent.act("Hello")]
        original_context_tokens = agent.stats.context_tokens
        assert original_context_tokens > 0
        assert len(agent.messages) > 1

        await agent.reload_with_initial_messages(base_config=config2)

        assert len(agent.messages) > 1
        assert agent.stats.context_tokens == original_context_tokens

    @pytest.mark.asyncio
    async def test_reload_updates_pricing_from_new_model(self, monkeypatch) -> None:
        monkeypatch.setenv("LECHAT_API_KEY", "mock-key")

        backend = FakeBackend(mock_llm_chunk(content="Response"))
        config_mistral = make_config(active_model="devstral-latest")
        agent = build_test_agent_loop(config=config_mistral, backend=backend)

        async for _ in agent.act("Hello"):
            pass

        assert agent.stats.input_price_per_million == 0.4
        assert agent.stats.output_price_per_million == 2.0

        config_other = make_config(active_model="strawberry")
        await agent.reload_with_initial_messages(base_config=config_other)

        assert agent.stats.input_price_per_million == 2.5
        assert agent.stats.output_price_per_million == 10.0

    @pytest.mark.asyncio
    async def test_reload_accumulates_tokens_across_configs(self, monkeypatch) -> None:
        monkeypatch.setenv("LECHAT_API_KEY", "mock-key")

        backend = FakeBackend([
            [mock_llm_chunk(content="First")],
            [mock_llm_chunk(content="After reload")],
        ])
        config1 = make_config(active_model="devstral-latest")
        agent = build_test_agent_loop(config=config1, backend=backend)

        async for _ in agent.act("Hello"):
            pass

        tokens_after_first = (
            agent.stats.session_prompt_tokens + agent.stats.session_completion_tokens
        )

        config2 = make_config(active_model="strawberry")
        await agent.reload_with_initial_messages(base_config=config2)

        async for _ in agent.act("Continue"):
            pass

        tokens_after_second = (
            agent.stats.session_prompt_tokens + agent.stats.session_completion_tokens
        )
        assert tokens_after_second > tokens_after_first


class TestReloadPreservesMessages:
    @pytest.mark.asyncio
    async def test_reload_preserves_conversation_messages(self) -> None:
        backend = FakeBackend(mock_llm_chunk(content="Response"))
        agent = build_test_agent_loop(config=make_config(), backend=backend)

        async for _ in agent.act("Hello"):
            pass

        assert len(agent.messages) == 3
        old_user_content = agent.messages[1].content
        old_assistant_content = agent.messages[2].content

        await agent.reload_with_initial_messages()

        assert len(agent.messages) == 3
        assert agent.messages[0].role == Role.system
        assert agent.messages[1].role == Role.user
        assert agent.messages[1].content == old_user_content
        assert agent.messages[2].role == Role.assistant
        assert agent.messages[2].content == old_assistant_content

    @pytest.mark.asyncio
    async def test_reload_updates_system_prompt_preserves_rest(self) -> None:
        backend = FakeBackend(mock_llm_chunk(content="Response"))
        config1 = make_config(system_prompt_id="tests")
        agent = build_test_agent_loop(config=config1, backend=backend)

        async for _ in agent.act("Hello"):
            pass

        old_system = agent.messages[0].content
        old_user = agent.messages[1].content

        config2 = make_config(system_prompt_id="cli")
        await agent.reload_with_initial_messages(base_config=config2)

        assert agent.messages[0].content != old_system
        assert agent.messages[1].content == old_user

    @pytest.mark.asyncio
    async def test_reload_with_no_messages_stays_empty(self) -> None:
        backend = FakeBackend([])
        agent = build_test_agent_loop(config=make_config(), backend=backend)

        assert len(agent.messages) == 1

        await agent.reload_with_initial_messages()

        assert len(agent.messages) == 1
        assert agent.messages[0].role == Role.system

    @pytest.mark.asyncio
    async def test_reload_notifies_observer_with_all_messages(
        self, observer_capture
    ) -> None:
        observed, observer = observer_capture
        backend = FakeBackend(mock_llm_chunk(content="Response"))
        agent = build_test_agent_loop(
            config=make_config(), message_observer=observer, backend=backend
        )

        async for _ in agent.act("Hello"):
            pass

        observed.clear()

        await agent.reload_with_initial_messages()

        assert len(observed) == 3
        assert observed[0].role == Role.system
        assert observed[1].role == Role.user
        assert observed[2].role == Role.assistant


class TestCompactStatsHandling:
    @pytest.mark.asyncio
    async def test_compact_preserves_cumulative_stats(self) -> None:
        backend = FakeBackend([
            [mock_llm_chunk(content="First response")],
            [mock_llm_chunk(content="<summary>")],
        ])
        agent = build_test_agent_loop(config=make_config(), backend=backend)

        async for _ in agent.act("Build something"):
            pass

        tokens_before_compact = agent.stats.session_prompt_tokens
        completions_before = agent.stats.session_completion_tokens
        steps_before = agent.stats.steps

        await agent.compact()

        # Cumulative stats include the compact turn
        assert agent.stats.session_prompt_tokens > tokens_before_compact
        assert agent.stats.session_completion_tokens > completions_before
        assert agent.stats.steps > steps_before

    @pytest.mark.asyncio
    async def test_compact_updates_context_tokens(self) -> None:
        backend = FakeBackend([
            [mock_llm_chunk(content="Long response " * 100)],
            [mock_llm_chunk(content="<summary>")],
        ])
        agent = build_test_agent_loop(config=make_config(), backend=backend)

        async for _ in agent.act("Do something complex"):
            pass

        context_before = agent.stats.context_tokens

        await agent.compact()

        assert agent.stats.context_tokens < context_before

    @pytest.mark.asyncio
    async def test_compact_preserves_tool_call_stats(self) -> None:
        backend = FakeBackend([
            [
                mock_llm_chunk(
                    content="Using tool",
                    tool_calls=[
                        ToolCall(
                            id="tc1",
                            index=0,
                            function=FunctionCall(
                                name="todo", arguments='{"action": "read"}'
                            ),
                        )
                    ],
                ),
                mock_llm_chunk(content=" todo"),
            ],
            [mock_llm_chunk(content="<summary>")],
        ])
        config = make_config(enabled_tools=["todo"])
        agent = build_test_agent_loop(
            config=config, agent_name=BuiltinAgentName.AUTO_APPROVE, backend=backend
        )

        async for _ in agent.act("Check todos"):
            pass

        assert agent.stats.tool_calls_succeeded == 1

        await agent.compact()

        assert agent.stats.tool_calls_succeeded == 1

    @pytest.mark.asyncio
    async def test_compact_resets_session_id(self) -> None:
        backend = FakeBackend([
            [mock_llm_chunk(content="Long response " * 100)],
            [mock_llm_chunk(content="<summary>")],
        ])
        agent = build_test_agent_loop(
            config=make_config(disable_logging=False), backend=backend
        )

        original_session_id = agent.session_id
        original_logger_session_id = agent.session_logger.session_id

        assert agent.session_id == original_logger_session_id

        async for _ in agent.act("Do something complex"):
            pass

        await agent.compact()

        assert agent.session_id != original_session_id
        assert agent.session_id == agent.session_logger.session_id


class TestAutoCompactIntegration:
    @pytest.mark.asyncio
    async def test_auto_compact_triggers_and_preserves_stats(self) -> None:
        observed: list[tuple[Role, str | None]] = []

        def observer(msg: LLMMessage) -> None:
            observed.append((msg.role, msg.content))

        backend = FakeBackend([
            [mock_llm_chunk(content="<summary>")],
            [mock_llm_chunk(content="<final>")],
        ])
        cfg = build_test_vibe_config(auto_compact_threshold=1)
        agent = build_test_agent_loop(
            config=cfg, message_observer=observer, backend=backend
        )
        agent.stats.context_tokens = 2

        events = [ev async for ev in agent.act("Hello")]

        assert len(events) == 4
        assert isinstance(events[0], UserMessageEvent)
        assert isinstance(events[1], CompactStartEvent)
        assert isinstance(events[2], CompactEndEvent)
        assert isinstance(events[3], AssistantEvent)

        start: CompactStartEvent = events[1]
        end: CompactEndEvent = events[2]
        final: AssistantEvent = events[3]

        assert start.current_context_tokens == 2
        assert start.threshold == 1
        assert end.old_context_tokens == 2
        assert end.new_context_tokens >= 1
        assert final.content == "<final>"

        roles = [r for r, _ in observed]
        assert roles == [Role.system, Role.user, Role.assistant]
        assert observed[1][1] is not None and "<summary>" in observed[1][1]


class TestClearHistoryFullReset:
    @pytest.mark.asyncio
    async def test_clear_history_fully_resets_stats(self) -> None:
        backend = FakeBackend(mock_llm_chunk(content="Response"))
        agent = build_test_agent_loop(config=make_config(), backend=backend)

        async for _ in agent.act("Hello"):
            pass

        assert agent.stats.session_prompt_tokens > 0
        assert agent.stats.steps > 0

        await agent.clear_history()

        assert agent.stats.session_prompt_tokens == 0
        assert agent.stats.session_completion_tokens == 0
        assert agent.stats.steps == 0

    @pytest.mark.asyncio
    async def test_clear_history_preserves_pricing(self) -> None:
        backend = FakeBackend(mock_llm_chunk(content="Response"))
        config = make_config(input_price=0.4, output_price=2.0)
        agent = build_test_agent_loop(config=config, backend=backend)

        async for _ in agent.act("Hello"):
            pass

        await agent.clear_history()

        assert agent.stats.input_price_per_million == 0.4
        assert agent.stats.output_price_per_million == 2.0

    @pytest.mark.asyncio
    async def test_clear_history_removes_messages(self) -> None:
        backend = FakeBackend(mock_llm_chunk(content="Response"))
        agent = build_test_agent_loop(config=make_config(), backend=backend)

        async for _ in agent.act("Hello"):
            pass

        assert len(agent.messages) == 3

        await agent.clear_history()

        assert len(agent.messages) == 1
        assert agent.messages[0].role == Role.system

    @pytest.mark.asyncio
    async def test_clear_history_resets_session_id(self) -> None:
        backend = FakeBackend(mock_llm_chunk(content="Response"))
        agent = build_test_agent_loop(
            config=make_config(disable_logging=False), backend=backend
        )

        original_session_id = agent.session_id
        original_logger_session_id = agent.session_logger.session_id

        assert agent.session_id == original_logger_session_id

        async for _ in agent.act("Hello"):
            pass

        await agent.clear_history()

        assert agent.session_id != original_session_id
        assert agent.session_id == agent.session_logger.session_id


class TestStatsEdgeCases:
    @pytest.mark.asyncio
    async def test_session_cost_approximation_on_model_change(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv("LECHAT_API_KEY", "mock-key")

        backend = FakeBackend(mock_llm_chunk(content="Response"))
        config1 = make_config(active_model="devstral-latest")
        agent = build_test_agent_loop(config=config1, backend=backend)

        async for _ in agent.act("Hello"):
            pass

        cost_before = agent.stats.session_cost

        config2 = make_config(active_model="strawberry")
        await agent.reload_with_initial_messages(base_config=config2)

        cost_after = agent.stats.session_cost

        assert cost_after > cost_before

    @pytest.mark.asyncio
    async def test_multiple_reloads_accumulate_correctly(self) -> None:
        backend = FakeBackend([
            [mock_llm_chunk(content="R1")],
            [mock_llm_chunk(content="R2")],
            [mock_llm_chunk(content="R3")],
        ])
        agent = build_test_agent_loop(config=make_config(), backend=backend)

        async for _ in agent.act("First"):
            pass
        tokens1 = agent.stats.session_total_llm_tokens

        await agent.reload_with_initial_messages()
        async for _ in agent.act("Second"):
            pass
        tokens2 = agent.stats.session_total_llm_tokens

        await agent.reload_with_initial_messages()
        async for _ in agent.act("Third"):
            pass
        tokens3 = agent.stats.session_total_llm_tokens

        assert tokens1 < tokens2 < tokens3

    @pytest.mark.asyncio
    async def test_compact_then_reload_preserves_both(self) -> None:
        backend = FakeBackend([
            [mock_llm_chunk(content="Initial response")],
            [mock_llm_chunk(content="<summary>")],
            [mock_llm_chunk(content="After reload")],
        ])
        agent = build_test_agent_loop(config=make_config(), backend=backend)

        async for _ in agent.act("Build something"):
            pass

        await agent.compact()
        tokens_after_compact = agent.stats.session_prompt_tokens

        await agent.reload_with_initial_messages()

        assert agent.stats.session_prompt_tokens == tokens_after_compact

        async for _ in agent.act("Continue"):
            pass

        assert agent.stats.session_prompt_tokens > tokens_after_compact

    @pytest.mark.asyncio
    async def test_reload_without_config_preserves_current(self) -> None:
        backend = FakeBackend([])
        original_config = make_config(active_model="devstral-latest")
        agent = build_test_agent_loop(config=original_config, backend=backend)

        await agent.reload_with_initial_messages(base_config=None)

        assert agent.config.active_model == "devstral-latest"

    @pytest.mark.asyncio
    async def test_reload_with_new_config_updates_it(self) -> None:
        backend = FakeBackend([])
        original_config = make_config(active_model="devstral-latest")
        agent = build_test_agent_loop(config=original_config, backend=backend)

        new_config = make_config(active_model="devstral-small")
        await agent.reload_with_initial_messages(base_config=new_config)

        assert agent.config.active_model == "devstral-small"
