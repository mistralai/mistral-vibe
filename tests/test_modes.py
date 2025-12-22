from __future__ import annotations

import pytest

from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.agent import Agent
from vibe.core.config import SessionLoggingConfig, VibeConfig
from vibe.core.llm.format import get_active_tool_classes
from vibe.core.modes import (
    MODE_CONFIGS,
    PLAN_MODE_TOOLS,
    AgentMode,
    ModeConfig,
    ModeSafety,
    get_mode_order,
    next_mode,
)
from vibe.core.tools.base import ToolPermission
from vibe.core.types import (
    FunctionCall,
    LLMChunk,
    LLMMessage,
    LLMUsage,
    Role,
    ToolCall,
    ToolResultEvent,
)


class TestModeSafety:
    def test_safety_enum_values(self) -> None:
        assert ModeSafety.SAFE == "safe"
        assert ModeSafety.NEUTRAL == "neutral"
        assert ModeSafety.DESTRUCTIVE == "destructive"
        assert ModeSafety.YOLO == "yolo"

    def test_default_mode_is_neutral(self) -> None:
        assert AgentMode.DEFAULT.safety == ModeSafety.NEUTRAL

    def test_auto_approve_mode_is_yolo(self) -> None:
        assert AgentMode.AUTO_APPROVE.safety == ModeSafety.YOLO

    def test_plan_mode_is_safe(self) -> None:
        assert AgentMode.PLAN.safety == ModeSafety.SAFE

    def test_accept_edits_mode_is_destructive(self) -> None:
        assert AgentMode.ACCEPT_EDITS.safety == ModeSafety.DESTRUCTIVE


class TestAgentMode:
    def test_all_modes_have_configs(self) -> None:
        for mode in AgentMode:
            assert mode in MODE_CONFIGS

    def test_display_name_property(self) -> None:
        assert AgentMode.DEFAULT.display_name == "Default"
        assert AgentMode.AUTO_APPROVE.display_name == "Auto Approve"
        assert AgentMode.PLAN.display_name == "Plan"
        assert AgentMode.ACCEPT_EDITS.display_name == "Accept Edits"

    def test_description_property(self) -> None:
        assert "approval" in AgentMode.DEFAULT.description.lower()
        assert "auto" in AgentMode.AUTO_APPROVE.description.lower()
        assert "read-only" in AgentMode.PLAN.description.lower()
        assert "edits" in AgentMode.ACCEPT_EDITS.description.lower()

    def test_auto_approve_property(self) -> None:
        assert AgentMode.DEFAULT.auto_approve is False
        assert AgentMode.AUTO_APPROVE.auto_approve is True
        assert AgentMode.PLAN.auto_approve is True
        assert AgentMode.ACCEPT_EDITS.auto_approve is False

    def test_from_string_valid(self) -> None:
        assert AgentMode.from_string("default") == AgentMode.DEFAULT
        assert AgentMode.from_string("AUTO_APPROVE") == AgentMode.AUTO_APPROVE
        assert AgentMode.from_string("Plan") == AgentMode.PLAN
        assert AgentMode.from_string("accept_edits") == AgentMode.ACCEPT_EDITS

    def test_from_string_invalid(self) -> None:
        assert AgentMode.from_string("invalid") is None
        assert AgentMode.from_string("") is None


class TestModeConfigOverrides:
    def test_default_mode_has_no_overrides(self) -> None:
        assert AgentMode.DEFAULT.config_overrides == {}

    def test_auto_approve_mode_has_no_overrides(self) -> None:
        assert AgentMode.AUTO_APPROVE.config_overrides == {}

    def test_plan_mode_restricts_tools(self) -> None:
        overrides = AgentMode.PLAN.config_overrides
        assert "enabled_tools" in overrides
        assert overrides["enabled_tools"] == PLAN_MODE_TOOLS

    def test_accept_edits_mode_sets_tool_permissions(self) -> None:
        overrides = AgentMode.ACCEPT_EDITS.config_overrides
        assert "tools" in overrides
        tools_config = overrides["tools"]
        assert "write_file" in tools_config
        assert "search_replace" in tools_config
        assert tools_config["write_file"]["permission"] == "always"
        assert tools_config["search_replace"]["permission"] == "always"


class TestModeCycling:
    def test_get_mode_order_includes_all_modes(self) -> None:
        order = get_mode_order()
        assert len(order) == 4
        assert AgentMode.DEFAULT in order
        assert AgentMode.AUTO_APPROVE in order
        assert AgentMode.PLAN in order
        assert AgentMode.ACCEPT_EDITS in order

    def test_next_mode_cycles_through_all(self) -> None:
        order = get_mode_order()
        current = order[0]
        visited = [current]
        for _ in range(len(order) - 1):
            current = next_mode(current)
            visited.append(current)
        assert len(set(visited)) == len(order)

    def test_next_mode_wraps_around(self) -> None:
        order = get_mode_order()
        last_mode = order[-1]
        first_mode = order[0]
        assert next_mode(last_mode) == first_mode


class TestModeConfig:
    def test_mode_config_defaults(self) -> None:
        config = ModeConfig(display_name="Test", description="Test mode")
        assert config.safety == ModeSafety.NEUTRAL
        assert config.auto_approve is False
        assert config.config_overrides == {}

    def test_mode_config_frozen(self) -> None:
        config = ModeConfig(display_name="Test", description="Test mode")
        with pytest.raises(AttributeError):
            config.display_name = "Changed"  # pyright: ignore[reportAttributeAccessIssue]


class TestAgentSwitchMode:
    @pytest.fixture
    def base_config(self) -> VibeConfig:
        return VibeConfig(
            session_logging=SessionLoggingConfig(enabled=False),
            auto_compact_threshold=0,
            include_project_context=False,
            include_prompt_detail=False,
        )

    @pytest.fixture
    def backend(self) -> FakeBackend:
        return FakeBackend([
            LLMChunk(
                message=LLMMessage(role=Role.assistant, content="Test response"),
                usage=LLMUsage(prompt_tokens=10, completion_tokens=5),
            )
        ])

    @pytest.mark.asyncio
    async def test_switch_to_plan_mode_restricts_tools(
        self, base_config: VibeConfig, backend: FakeBackend
    ) -> None:
        agent = Agent(base_config, mode=AgentMode.DEFAULT, backend=backend)
        initial_tools = get_active_tool_classes(agent.tool_manager, agent.config)
        initial_tool_names = {t.get_name() for t in initial_tools}
        assert len(initial_tool_names) > len(PLAN_MODE_TOOLS)

        await agent.switch_mode(AgentMode.PLAN)

        plan_tools = get_active_tool_classes(agent.tool_manager, agent.config)
        plan_tool_names = {t.get_name() for t in plan_tools}
        assert plan_tool_names == set(PLAN_MODE_TOOLS)
        assert agent.mode == AgentMode.PLAN

    @pytest.mark.asyncio
    async def test_switch_from_plan_to_normal_restores_tools(
        self, base_config: VibeConfig, backend: FakeBackend
    ) -> None:
        plan_config = VibeConfig.model_validate({
            **base_config.model_dump(),
            **AgentMode.PLAN.config_overrides,
        })
        agent = Agent(plan_config, mode=AgentMode.PLAN, backend=backend)
        plan_tools = get_active_tool_classes(agent.tool_manager, agent.config)
        assert len(plan_tools) == len(PLAN_MODE_TOOLS)

        await agent.switch_mode(AgentMode.DEFAULT)

        normal_tools = get_active_tool_classes(agent.tool_manager, agent.config)
        assert len(normal_tools) > len(PLAN_MODE_TOOLS)
        assert agent.mode == AgentMode.DEFAULT

    @pytest.mark.asyncio
    async def test_switch_mode_preserves_conversation_history(
        self, base_config: VibeConfig, backend: FakeBackend
    ) -> None:
        agent = Agent(base_config, mode=AgentMode.DEFAULT, backend=backend)
        user_msg = LLMMessage(role=Role.user, content="Hello")
        assistant_msg = LLMMessage(role=Role.assistant, content="Hi there")
        agent.messages.append(user_msg)
        agent.messages.append(assistant_msg)

        await agent.switch_mode(AgentMode.PLAN)

        assert len(agent.messages) == 3  # system + user + assistant
        assert agent.messages[1].content == "Hello"
        assert agent.messages[2].content == "Hi there"

    @pytest.mark.asyncio
    async def test_switch_to_same_mode_is_noop(
        self, base_config: VibeConfig, backend: FakeBackend
    ) -> None:
        agent = Agent(base_config, mode=AgentMode.DEFAULT, backend=backend)
        original_config = agent.config

        await agent.switch_mode(AgentMode.DEFAULT)

        assert agent.config is original_config
        assert agent.mode == AgentMode.DEFAULT


class TestAcceptEditsMode:
    def test_accept_edits_config_sets_write_file_always(self) -> None:
        overrides = AgentMode.ACCEPT_EDITS.config_overrides
        assert overrides["tools"]["write_file"]["permission"] == "always"

    def test_accept_edits_config_sets_search_replace_always(self) -> None:
        overrides = AgentMode.ACCEPT_EDITS.config_overrides
        assert overrides["tools"]["search_replace"]["permission"] == "always"

    def test_accept_edits_mode_not_auto_approve(self) -> None:
        assert AgentMode.ACCEPT_EDITS.auto_approve is False

    @pytest.mark.asyncio
    async def test_accept_edits_mode_auto_approves_write_file(self) -> None:
        backend = FakeBackend([])

        config = VibeConfig(
            session_logging=SessionLoggingConfig(enabled=False),
            auto_compact_threshold=0,
            enabled_tools=["write_file"],
            **AgentMode.ACCEPT_EDITS.config_overrides,
        )
        agent = Agent(config, mode=AgentMode.ACCEPT_EDITS, backend=backend)

        perm = agent.tool_manager.get_tool_config("write_file").permission
        assert perm == ToolPermission.ALWAYS

    @pytest.mark.asyncio
    async def test_accept_edits_mode_requires_approval_for_other_tools(self) -> None:
        backend = FakeBackend([])

        config = VibeConfig(
            session_logging=SessionLoggingConfig(enabled=False),
            auto_compact_threshold=0,
            enabled_tools=["bash"],
            **AgentMode.ACCEPT_EDITS.config_overrides,
        )
        agent = Agent(config, mode=AgentMode.ACCEPT_EDITS, backend=backend)

        perm = agent.tool_manager.get_tool_config("bash").permission
        assert perm == ToolPermission.ASK


class TestPlanModeToolRestriction:
    @pytest.mark.asyncio
    async def test_plan_mode_only_exposes_read_tools_to_llm(self) -> None:
        backend = FakeBackend([
            LLMChunk(
                message=LLMMessage(role=Role.assistant, content="ok"),
                usage=LLMUsage(prompt_tokens=10, completion_tokens=5),
            )
        ])
        config = VibeConfig(
            session_logging=SessionLoggingConfig(enabled=False),
            auto_compact_threshold=0,
            **AgentMode.PLAN.config_overrides,
        )
        agent = Agent(config, mode=AgentMode.PLAN, backend=backend)

        active_tools = get_active_tool_classes(agent.tool_manager, agent.config)
        tool_names = {t.get_name() for t in active_tools}

        assert "bash" not in tool_names
        assert "write_file" not in tool_names
        assert "search_replace" not in tool_names
        for plan_tool in PLAN_MODE_TOOLS:
            assert plan_tool in tool_names

    @pytest.mark.asyncio
    async def test_plan_mode_rejects_non_plan_tool_call(self) -> None:
        tool_call = ToolCall(
            id="call_1",
            index=0,
            function=FunctionCall(name="bash", arguments='{"command": "ls"}'),
        )
        backend = FakeBackend([
            mock_llm_chunk(content="Let me run bash", tool_calls=[tool_call]),
            mock_llm_chunk(content="Tool not available"),
        ])

        config = VibeConfig(
            session_logging=SessionLoggingConfig(enabled=False),
            auto_compact_threshold=0,
            **AgentMode.PLAN.config_overrides,
        )
        agent = Agent(config, mode=AgentMode.PLAN, backend=backend)

        events = [ev async for ev in agent.act("Run ls")]

        tool_result = next((e for e in events if isinstance(e, ToolResultEvent)), None)
        assert tool_result is not None
        assert tool_result.error is not None
        assert (
            "not found" in tool_result.error.lower()
            or "error" in tool_result.error.lower()
        )
