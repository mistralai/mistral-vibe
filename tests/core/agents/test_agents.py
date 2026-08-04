from __future__ import annotations

import pytest

from tests.conftest import ConfigBuilder, OrchestratorLoader
from vibe.core.agents.manager import AgentManager
from vibe.core.agents.models import BUILTIN_AGENTS, EXPLORE, AgentSafety, AgentType
from vibe.core.config import VibeConfigSchema


class TestAgentProfile:
    def test_explore_agent_is_subagent(self) -> None:
        """Test that EXPLORE agent has SUBAGENT type."""
        assert EXPLORE.agent_type == AgentType.SUBAGENT

    def test_explore_agent_has_safe_safety(self) -> None:
        """Test that EXPLORE agent has SAFE safety level."""
        assert EXPLORE.safety == AgentSafety.SAFE

    def test_explore_agent_has_enabled_tools(self) -> None:
        """Test that EXPLORE agent has expected enabled tools."""
        enabled_tools = EXPLORE.overrides.get("enabled_tools", [])
        assert "grep" in enabled_tools
        assert "read_file" in enabled_tools

    def test_builtin_agents_contains_explore(self) -> None:
        """Test that BUILTIN_AGENTS includes explore."""
        assert "explore" in BUILTIN_AGENTS
        assert BUILTIN_AGENTS["explore"] is EXPLORE


class TestAgentManager:
    @pytest.fixture
    def manager(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> AgentManager:
        config = build_config()
        return AgentManager(load_orchestrator(config))

    def test_get_subagents_returns_only_subagents(self, manager: AgentManager) -> None:
        """Test that only SUBAGENT type agents are returned."""
        subagents = manager.get_subagents()

        for agent in subagents:
            assert agent.agent_type == AgentType.SUBAGENT

    def test_get_subagents_includes_explore(self, manager: AgentManager) -> None:
        """Test that EXPLORE is included in subagents."""
        subagents = manager.get_subagents()
        names = [a.name for a in subagents]

        assert "explore" in names

    def test_get_subagents_excludes_agents(self, manager: AgentManager) -> None:
        """Test that AGENT type agents are not returned."""
        subagents = manager.get_subagents()
        names = [a.name for a in subagents]

        # These are AGENT type
        assert "default" not in names
        assert "plan" not in names
        assert "auto-approve" not in names

    def test_get_builtin_agent(self, manager: AgentManager) -> None:
        """Test getting a builtin agent by name."""
        agent = manager.get_agent("explore")

        assert agent is EXPLORE
        assert agent.agent_type == AgentType.SUBAGENT

    def test_get_nonexistent_agent_raises(self, manager: AgentManager) -> None:
        """Test that getting a nonexistent agent raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            manager.get_agent("nonexistent-agent")

    def test_get_default_agent(self, manager: AgentManager) -> None:
        """Test getting the default agent."""
        agent = manager.get_agent("default")

        assert agent.name == "default"
        assert agent.agent_type == AgentType.AGENT

    def test_initial_agent_rejects_subagent(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        """Test that creating AgentManager with a subagent as initial_agent raises."""
        config = build_config()
        with pytest.raises(ValueError, match="cannot be used as the primary agent"):
            AgentManager(load_orchestrator(config), initial_agent="explore")

    def test_initial_agent_accepts_subagent_when_allowed(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        """Test that allow_subagent=True permits subagent as initial_agent."""
        config = build_config()
        manager = AgentManager(
            load_orchestrator(config), initial_agent="explore", allow_subagent=True
        )
        assert manager.active_profile.name == "explore"

    def test_initial_agent_accepts_agent_type(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        """Test that creating AgentManager with an agent-type agent works."""
        config = build_config()
        manager = AgentManager(load_orchestrator(config), initial_agent="plan")
        assert manager.active_profile.name == "plan"

    def test_initial_agent_raises_when_agent_is_disabled(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config(disabled_agents=["plan"])
        with pytest.raises(ValueError, match="disabled_agents") as exc_info:
            AgentManager(load_orchestrator(config), initial_agent="plan")
        message = str(exc_info.value)
        assert "default_agent" not in message
        assert message.startswith("Agent 'plan'")

    def test_explicit_agent_excluded_by_enabled_agents_does_not_blame_default(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config(enabled_agents=["default"])
        with pytest.raises(ValueError, match="enabled_agents") as exc_info:
            AgentManager(load_orchestrator(config), initial_agent="plan")
        message = str(exc_info.value)
        assert "default_agent" not in message
        assert message.startswith("Agent 'plan'")

    def test_initial_agent_raises_when_agent_does_not_exist(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config()
        with pytest.raises(ValueError, match="not found"):
            AgentManager(load_orchestrator(config), initial_agent="nonexistent-agent")

    def test_default_agent_excluded_by_enabled_agents_raises_config_contradiction(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config(enabled_agents=["plan"])
        with pytest.raises(ValueError, match="enabled_agents") as exc_info:
            AgentManager(load_orchestrator(config))
        message = str(exc_info.value)
        assert "default" in message
        assert "default_agent" in message

    def test_default_agent_excluded_by_disabled_agents_raises_config_contradiction(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config(disabled_agents=["default"])
        with pytest.raises(ValueError, match="disabled_agents") as exc_info:
            AgentManager(load_orchestrator(config))
        assert "default_agent" in str(exc_info.value)

    def test_disabled_agents_ignored_entirely_when_enabled_agents_set(
        self,
        caplog: pytest.LogCaptureFixture,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config(enabled_agents=["plan"], disabled_agents=["plan"])
        with caplog.at_level("WARNING"):
            manager = AgentManager(load_orchestrator(config), initial_agent="plan")
        assert manager.active_profile.name == "plan"
        assert caplog.text == ""

    def test_install_required_agent_reports_install_not_disabled_agents(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        # 'lean' is install_required and enabled but not installed: the message
        # must point to installation, not blame disabled_agents.
        config = build_config(enabled_agents=["lean"])
        with pytest.raises(ValueError, match="requires installation") as exc_info:
            AgentManager(load_orchestrator(config), initial_agent="lean")
        assert "disabled_agents" not in str(exc_info.value)
