from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.agents.manager import AgentManager
from vibe.core.agents.models import (
    BUILTIN_AGENTS,
    PLAN_AGENT_TOOLS,
    AgentProfile,
    AgentSafety,
    AgentType,
    BuiltinAgentName,
    _deep_merge,
)
from vibe.core.config import VibeConfig
from vibe.core.paths.config_paths import ConfigPath
from vibe.core.paths.global_paths import GlobalPath
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


class TestDeepMerge:
    def test_simple_merge(self) -> None:
        base = {"a": 1, "b": 2}
        override = {"c": 3}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 2, "c": 3}

    def test_override_existing_key(self) -> None:
        base = {"a": 1, "b": 2}
        override = {"b": 3}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 3}

    def test_nested_dict_merge(self) -> None:
        base = {"a": {"x": 1, "y": 2}}
        override = {"a": {"y": 3, "z": 4}}
        result = _deep_merge(base, override)
        assert result == {"a": {"x": 1, "y": 3, "z": 4}}

    def test_deeply_nested_merge(self) -> None:
        base = {"a": {"b": {"c": 1}}}
        override = {"a": {"b": {"d": 2}}}
        result = _deep_merge(base, override)
        assert result == {"a": {"b": {"c": 1, "d": 2}}}

    def test_override_dict_with_non_dict(self) -> None:
        base = {"a": {"x": 1}}
        override = {"a": "replaced"}
        result = _deep_merge(base, override)
        assert result == {"a": "replaced"}

    def test_override_non_dict_with_dict(self) -> None:
        base = {"a": "string"}
        override = {"a": {"x": 1}}
        result = _deep_merge(base, override)
        assert result == {"a": {"x": 1}}

    def test_preserves_original_base(self) -> None:
        base = {"a": 1, "b": {"c": 2}}
        override = {"b": {"d": 3}}
        _deep_merge(base, override)
        assert base == {"a": 1, "b": {"c": 2}}

    def test_empty_override(self) -> None:
        base = {"a": 1, "b": 2}
        override: dict = {}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 2}

    def test_empty_base(self) -> None:
        base: dict = {}
        override = {"a": 1}
        result = _deep_merge(base, override)
        assert result == {"a": 1}

    def test_lists_are_overridden_not_merged(self) -> None:
        """Lists should be replaced entirely, not merged element-by-element."""
        base = {"tools": ["read_file", "grep", "bash"]}
        override = {"tools": ["write_file"]}
        result = _deep_merge(base, override)
        assert result == {"tools": ["write_file"]}

    def test_nested_lists_are_overridden_not_merged(self) -> None:
        """Nested lists in dicts should also be replaced, not merged."""
        base = {"config": {"enabled_tools": ["a", "b", "c"], "other": 1}}
        override = {"config": {"enabled_tools": ["x", "y"]}}
        result = _deep_merge(base, override)
        assert result == {"config": {"enabled_tools": ["x", "y"], "other": 1}}


class TestAgentSafety:
    def test_safety_enum_values(self) -> None:
        assert AgentSafety.SAFE == "safe"
        assert AgentSafety.NEUTRAL == "neutral"
        assert AgentSafety.DESTRUCTIVE == "destructive"
        assert AgentSafety.YOLO == "yolo"

    def test_default_agent_is_neutral(self) -> None:
        assert BUILTIN_AGENTS[BuiltinAgentName.DEFAULT].safety == AgentSafety.NEUTRAL

    def test_auto_approve_agent_is_yolo(self) -> None:
        assert BUILTIN_AGENTS[BuiltinAgentName.AUTO_APPROVE].safety == AgentSafety.YOLO

    def test_plan_agent_is_safe(self) -> None:
        assert BUILTIN_AGENTS[BuiltinAgentName.PLAN].safety == AgentSafety.SAFE

    def test_accept_edits_agent_is_destructive(self) -> None:
        assert (
            BUILTIN_AGENTS[BuiltinAgentName.ACCEPT_EDITS].safety
            == AgentSafety.DESTRUCTIVE
        )


class TestAgentProfile:
    def test_all_builtin_agents_exist(self) -> None:
        assert set(BUILTIN_AGENTS.keys()) == set(BuiltinAgentName)

    def test_display_name_property(self) -> None:
        assert BUILTIN_AGENTS[BuiltinAgentName.DEFAULT].display_name == "Default"
        assert (
            BUILTIN_AGENTS[BuiltinAgentName.AUTO_APPROVE].display_name == "Auto Approve"
        )
        assert BUILTIN_AGENTS[BuiltinAgentName.PLAN].display_name == "Plan"
        assert (
            BUILTIN_AGENTS[BuiltinAgentName.ACCEPT_EDITS].display_name == "Accept Edits"
        )

    def test_description_property(self) -> None:
        assert (
            "approval" in BUILTIN_AGENTS[BuiltinAgentName.DEFAULT].description.lower()
        )
        assert (
            "auto" in BUILTIN_AGENTS[BuiltinAgentName.AUTO_APPROVE].description.lower()
        )
        assert "read-only" in BUILTIN_AGENTS[BuiltinAgentName.PLAN].description.lower()
        assert (
            "edits" in BUILTIN_AGENTS[BuiltinAgentName.ACCEPT_EDITS].description.lower()
        )

    def test_explore_is_subagent(self) -> None:
        assert BUILTIN_AGENTS[BuiltinAgentName.EXPLORE].agent_type == AgentType.SUBAGENT

    def test_agents(self) -> None:
        agents = [
            name
            for name, profile in BUILTIN_AGENTS.items()
            if profile.agent_type == AgentType.AGENT
        ]
        assert set(agents) == {
            BuiltinAgentName.DEFAULT,
            BuiltinAgentName.PLAN,
            BuiltinAgentName.ACCEPT_EDITS,
            BuiltinAgentName.AUTO_APPROVE,
        }


class TestAgentApplyToConfig:
    def test_custom_prompt_found_in_global_when_missing_from_project(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression test for https://github.com/mistralai/mistral-vibe/issues/288

        When a custom prompt .md file is absent from the project-local prompts
        directory, the system_prompt property should fall back to the global
        ~/.vibe/prompts/ directory and load the file from there.
        """
        project_prompts = tmp_path / "project" / ".vibe" / "prompts"
        project_prompts.mkdir(parents=True)

        global_prompts = tmp_path / "home" / ".vibe" / "prompts"
        global_prompts.mkdir(parents=True)
        (global_prompts / "cc.md").write_text("Global custom prompt")

        monkeypatch.setattr(
            "vibe.core.config.PROMPTS_DIR", ConfigPath(lambda: project_prompts)
        )
        monkeypatch.setattr(
            "vibe.core.config.GLOBAL_PROMPTS_DIR", GlobalPath(lambda: global_prompts)
        )

        base = VibeConfig(include_project_context=False, include_prompt_detail=False)
        agent = AgentProfile(
            name="cc",
            display_name="Cc",
            description="",
            safety=AgentSafety.NEUTRAL,
            overrides={"system_prompt_id": "cc"},
        )
        result = agent.apply_to_config(base)
        assert result.system_prompt_id == "cc"
        assert result.system_prompt == "Global custom prompt"


class TestAgentProfileOverrides:
    def test_default_agent_has_no_overrides(self) -> None:
        assert BUILTIN_AGENTS[BuiltinAgentName.DEFAULT].overrides == {}

    def test_auto_approve_agent_sets_auto_approve(self) -> None:
        overrides = BUILTIN_AGENTS[BuiltinAgentName.AUTO_APPROVE].overrides
        assert overrides.get("auto_approve") is True

    def test_plan_agent_restricts_tools(self) -> None:
        overrides = BUILTIN_AGENTS[BuiltinAgentName.PLAN].overrides
        assert "enabled_tools" in overrides
        assert overrides["enabled_tools"] == PLAN_AGENT_TOOLS

    def test_accept_edits_agent_sets_tool_permissions(self) -> None:
        overrides = BUILTIN_AGENTS[BuiltinAgentName.ACCEPT_EDITS].overrides
        assert "tools" in overrides
        tools_config = overrides["tools"]
        assert "write_file" in tools_config
        assert "search_replace" in tools_config
        assert tools_config["write_file"]["permission"] == "always"
        assert tools_config["search_replace"]["permission"] == "always"


class TestAgentManagerCycling:
    @pytest.fixture
    def base_config(self) -> VibeConfig:
        return build_test_vibe_config(
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

    def test_get_agent_order_includes_primary_agents(
        self, base_config: VibeConfig, backend: FakeBackend
    ) -> None:
        agent = build_test_agent_loop(
            config=base_config, agent_name=BuiltinAgentName.DEFAULT, backend=backend
        )
        order = agent.agent_manager.get_agent_order()
        assert len(order) == 4
        assert BuiltinAgentName.DEFAULT in order
        assert BuiltinAgentName.AUTO_APPROVE in order
        assert BuiltinAgentName.PLAN in order
        assert BuiltinAgentName.ACCEPT_EDITS in order

    def test_next_agent_cycles_through_all(
        self, base_config: VibeConfig, backend: FakeBackend
    ) -> None:
        agent = build_test_agent_loop(
            config=base_config, agent_name=BuiltinAgentName.DEFAULT, backend=backend
        )
        order = agent.agent_manager.get_agent_order()
        current = agent.agent_manager.active_profile
        visited = [current.name]
        for _ in range(len(order) - 1):
            current = agent.agent_manager.next_agent(current)
            visited.append(current.name)
        assert len(set(visited)) == len(order)

    def test_next_agent_wraps_around(
        self, base_config: VibeConfig, backend: FakeBackend
    ) -> None:
        agent = build_test_agent_loop(
            config=base_config, agent_name=BuiltinAgentName.DEFAULT, backend=backend
        )
        order = agent.agent_manager.get_agent_order()
        last_profile = agent.agent_manager.get_agent(order[-1])
        first_profile = agent.agent_manager.get_agent(order[0])
        assert agent.agent_manager.next_agent(last_profile).name == first_profile.name


class TestAgentProfileConfig:
    def test_agent_profile_frozen(self) -> None:
        profile = AgentProfile(
            name="test",
            display_name="Test",
            description="Test agent",
            safety=AgentSafety.NEUTRAL,
        )
        with pytest.raises(AttributeError):
            profile.name = "changed"  # pyright: ignore[reportAttributeAccessIssue]


class TestAgentSwitchAgent:
    @pytest.fixture
    def base_config(self) -> VibeConfig:
        return build_test_vibe_config(
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
    async def test_switch_to_plan_agent_restricts_tools(
        self, base_config: VibeConfig, backend: FakeBackend
    ) -> None:
        agent = build_test_agent_loop(
            config=base_config, agent_name=BuiltinAgentName.DEFAULT, backend=backend
        )
        initial_tool_names = set(agent.tool_manager.available_tools.keys())
        assert len(initial_tool_names) > len(PLAN_AGENT_TOOLS)

        await agent.switch_agent(BuiltinAgentName.PLAN)

        plan_tool_names = set(agent.tool_manager.available_tools.keys())
        assert plan_tool_names == set(PLAN_AGENT_TOOLS)
        assert agent.agent_profile.name == BuiltinAgentName.PLAN

    @pytest.mark.asyncio
    async def test_switch_from_plan_to_default_restores_tools(
        self, base_config: VibeConfig, backend: FakeBackend
    ) -> None:
        agent = build_test_agent_loop(
            config=base_config, agent_name=BuiltinAgentName.PLAN, backend=backend
        )
        assert len(agent.tool_manager.available_tools) == len(PLAN_AGENT_TOOLS)

        await agent.switch_agent(BuiltinAgentName.DEFAULT)

        assert len(agent.tool_manager.available_tools) > len(PLAN_AGENT_TOOLS)
        assert agent.agent_profile.name == BuiltinAgentName.DEFAULT

    @pytest.mark.asyncio
    async def test_switch_agent_preserves_conversation_history(
        self, base_config: VibeConfig, backend: FakeBackend
    ) -> None:
        agent = build_test_agent_loop(
            config=base_config, agent_name=BuiltinAgentName.DEFAULT, backend=backend
        )
        user_msg = LLMMessage(role=Role.user, content="Hello")
        assistant_msg = LLMMessage(role=Role.assistant, content="Hi there")
        agent.messages.append(user_msg)
        agent.messages.append(assistant_msg)

        await agent.switch_agent(BuiltinAgentName.PLAN)

        assert len(agent.messages) == 3  # system + user + assistant
        assert agent.messages[1].content == "Hello"
        assert agent.messages[2].content == "Hi there"

    @pytest.mark.asyncio
    async def test_switch_to_same_agent_is_noop(
        self, base_config: VibeConfig, backend: FakeBackend
    ) -> None:
        agent = build_test_agent_loop(
            config=base_config, agent_name=BuiltinAgentName.DEFAULT, backend=backend
        )
        original_config = agent.config

        await agent.switch_agent(BuiltinAgentName.DEFAULT)

        assert agent.config is original_config
        assert agent.agent_profile.name == BuiltinAgentName.DEFAULT


class TestAcceptEditsAgent:
    def test_accept_edits_config_sets_write_file_always(self) -> None:
        overrides = BUILTIN_AGENTS[BuiltinAgentName.ACCEPT_EDITS].overrides
        assert overrides["tools"]["write_file"]["permission"] == "always"

    def test_accept_edits_config_sets_search_replace_always(self) -> None:
        overrides = BUILTIN_AGENTS[BuiltinAgentName.ACCEPT_EDITS].overrides
        assert overrides["tools"]["search_replace"]["permission"] == "always"

    @pytest.mark.asyncio
    async def test_accept_edits_agent_auto_approves_write_file(self) -> None:
        backend = FakeBackend([])

        config = build_test_vibe_config(
            auto_compact_threshold=0, enabled_tools=["write_file"]
        )
        agent = build_test_agent_loop(
            config=config, agent_name=BuiltinAgentName.ACCEPT_EDITS, backend=backend
        )

        perm = agent.tool_manager.get_tool_config("write_file").permission
        assert perm == ToolPermission.ALWAYS

    @pytest.mark.asyncio
    async def test_accept_edits_agent_requires_approval_for_other_tools(self) -> None:
        backend = FakeBackend([])

        config = build_test_vibe_config(
            auto_compact_threshold=0, enabled_tools=["bash"]
        )
        agent = build_test_agent_loop(
            config=config, agent_name=BuiltinAgentName.ACCEPT_EDITS, backend=backend
        )

        perm = agent.tool_manager.get_tool_config("bash").permission
        assert perm == ToolPermission.ASK


class TestPlanAgentToolRestriction:
    @pytest.mark.asyncio
    async def test_plan_agent_only_exposes_read_tools_to_llm(self) -> None:
        backend = FakeBackend([
            LLMChunk(
                message=LLMMessage(role=Role.assistant, content="ok"),
                usage=LLMUsage(prompt_tokens=10, completion_tokens=5),
            )
        ])
        config = build_test_vibe_config(auto_compact_threshold=0)
        agent = build_test_agent_loop(
            config=config, agent_name=BuiltinAgentName.PLAN, backend=backend
        )

        tool_names = set(agent.tool_manager.available_tools.keys())

        assert "bash" not in tool_names
        assert "write_file" not in tool_names
        assert "search_replace" not in tool_names
        for plan_tool in PLAN_AGENT_TOOLS:
            assert plan_tool in tool_names

    @pytest.mark.asyncio
    async def test_plan_agent_rejects_non_plan_tool_call(self) -> None:
        tool_call = ToolCall(
            id="call_1",
            index=0,
            function=FunctionCall(name="bash", arguments='{"command": "ls"}'),
        )
        backend = FakeBackend([
            mock_llm_chunk(content="Let me run bash", tool_calls=[tool_call]),
            mock_llm_chunk(content="Tool not available"),
        ])

        config = build_test_vibe_config(auto_compact_threshold=0)
        agent = build_test_agent_loop(
            config=config, agent_name=BuiltinAgentName.PLAN, backend=backend
        )

        events = [ev async for ev in agent.act("Run ls")]

        tool_result = next((e for e in events if isinstance(e, ToolResultEvent)), None)
        assert tool_result is not None
        assert tool_result.error is not None
        assert (
            "not found" in tool_result.error.lower()
            or "error" in tool_result.error.lower()
        )


class TestAgentManagerFiltering:
    def test_enabled_agents_filters_to_only_enabled(self) -> None:
        config = build_test_vibe_config(
            include_project_context=False,
            include_prompt_detail=False,
            enabled_agents=["default", "plan"],
        )
        manager = AgentManager(lambda: config)

        agents = manager.available_agents
        assert len(agents) < len(manager._available)
        assert "default" in agents
        assert "plan" in agents
        assert "auto-approve" not in agents
        assert "accept-edits" not in agents

    def test_disabled_agents_excludes_disabled(self) -> None:
        config = build_test_vibe_config(
            include_project_context=False,
            include_prompt_detail=False,
            disabled_agents=["auto-approve", "accept-edits"],
        )
        manager = AgentManager(lambda: config)

        agents = manager.available_agents
        assert len(agents) < len(manager._available)
        assert "default" in agents
        assert "plan" in agents
        assert "auto-approve" not in agents
        assert "accept-edits" not in agents

    def test_enabled_agents_takes_precedence_over_disabled(self) -> None:
        config = build_test_vibe_config(
            include_project_context=False,
            include_prompt_detail=False,
            enabled_agents=["default"],
            disabled_agents=["default"],  # Should be ignored
        )
        manager = AgentManager(lambda: config)

        agents = manager.available_agents
        assert len(agents) == 1
        assert "default" in agents

    def test_glob_pattern_matching(self) -> None:
        config = build_test_vibe_config(
            include_project_context=False,
            include_prompt_detail=False,
            disabled_agents=["auto-*", "accept-*"],
        )
        manager = AgentManager(lambda: config)

        agents = manager.available_agents
        assert "default" in agents
        assert "plan" in agents
        assert "auto-approve" not in agents
        assert "accept-edits" not in agents

    def test_regex_pattern_matching(self) -> None:
        config = build_test_vibe_config(
            include_project_context=False,
            include_prompt_detail=False,
            enabled_agents=["re:^(default|plan)$"],
        )
        manager = AgentManager(lambda: config)

        agents = manager.available_agents
        assert len(agents) == 2
        assert "default" in agents
        assert "plan" in agents

    def test_empty_enabled_agents_returns_all(self) -> None:
        config = build_test_vibe_config(
            include_project_context=False,
            include_prompt_detail=False,
            enabled_agents=[],
        )
        manager = AgentManager(lambda: config)

        agents = manager.available_agents
        assert "default" in agents
        assert "plan" in agents
        assert "auto-approve" in agents
        assert "explore" in agents

    def test_get_subagents_respects_filtering(self) -> None:
        config = build_test_vibe_config(
            include_project_context=False,
            include_prompt_detail=False,
            disabled_agents=["explore"],
        )
        manager = AgentManager(lambda: config)

        subagents = manager.get_subagents()
        names = [a.name for a in subagents]
        assert "explore" not in names


class TestAgentLoopInitialization:
    def test_agent_system_prompt_id_is_applied_on_init(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project_prompts = tmp_path / "project" / ".vibe" / "prompts"
        project_prompts.mkdir(parents=True)

        global_prompts = tmp_path / "home" / ".vibe" / "prompts"
        global_prompts.mkdir(parents=True)
        custom_prompt_content = "CUSTOM_AGENT_PROMPT_MARKER"
        (global_prompts / "custom_agent.md").write_text(custom_prompt_content)

        monkeypatch.setattr(
            "vibe.core.config.PROMPTS_DIR", ConfigPath(lambda: project_prompts)
        )
        monkeypatch.setattr(
            "vibe.core.config.GLOBAL_PROMPTS_DIR", GlobalPath(lambda: global_prompts)
        )

        custom_agent = AgentProfile(
            name="custom_test_agent",
            display_name="Custom Test",
            description="Test agent with custom system prompt",
            safety=AgentSafety.NEUTRAL,
            overrides={"system_prompt_id": "custom_agent"},
        )
        patched_agents = {**BUILTIN_AGENTS, "custom_test_agent": custom_agent}
        monkeypatch.setattr("vibe.core.agents.models.BUILTIN_AGENTS", patched_agents)
        monkeypatch.setattr("vibe.core.agents.manager.BUILTIN_AGENTS", patched_agents)

        config = build_test_vibe_config(
            include_project_context=False, include_prompt_detail=False
        )
        assert config.system_prompt_id == "cli", (
            "Base config should use default 'cli' prompt"
        )

        agent_loop = build_test_agent_loop(
            config=config, agent_name="custom_test_agent"
        )

        assert agent_loop.config.system_prompt_id == "custom_agent", (
            "Merged config should have the agent's system_prompt_id override"
        )

        system_message = agent_loop.messages[0]
        assert system_message.role == Role.system
        assert system_message.content is not None
        assert custom_prompt_content in system_message.content, (
            f"System message should contain custom prompt content. "
            f"Expected '{custom_prompt_content}' to be in system message."
        )
