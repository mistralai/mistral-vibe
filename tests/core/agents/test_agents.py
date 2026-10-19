from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import ConfigBuilder, OrchestratorLoader
from vibe.core.agents.manager import AgentManager
from vibe.core.agents.models import (
    BUILTIN_AGENTS,
    EXPLORE,
    AgentProfile,
    AgentSafety,
    AgentType,
)
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
        assert "skill" in enabled_tools

    def test_a_profile_remembers_the_file_it_was_parsed_from(
        self, tmp_path: Path
    ) -> None:
        """The Unified Harness advertises a subagent with the path behind it."""
        path = tmp_path / "reviewer.toml"
        path.write_text('description = "Reviews"\n', encoding="utf-8")

        assert AgentProfile.from_toml(path).source_path == path

    def test_a_builtin_profile_has_no_source_file(self) -> None:
        assert EXPLORE.source_path is None

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
        assert "ask" not in names
        assert "accept-edits" not in names
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

    def test_get_ask_agent(self, manager: AgentManager) -> None:
        agent = manager.get_agent("ask")

        assert agent.name == "ask"
        assert agent.agent_type == AgentType.AGENT

    def test_smart_approve_hidden_from_the_picker_by_default(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        """Smart approve ships dark: without the flag it is not in the picker/cycle."""
        manager = AgentManager(load_orchestrator(build_config()))

        assert "smart-approve" not in manager.available_agents
        assert "smart-approve" not in manager.get_agent_order()

    def test_smart_approve_available_flag_exposes_it_in_the_picker(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        """The availability flag adds smart approve to the picker/cycle."""
        config = build_config(smart_approve_available=True)
        manager = AgentManager(load_orchestrator(config))

        assert "smart-approve" in manager.available_agents
        assert "smart-approve" in manager.get_agent_order()

    def test_smart_approve_default_flag_offers_and_defaults(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        """The default flag both exposes smart approve and makes it the resolved default."""
        config = build_config(smart_approve_default=True)
        manager = AgentManager(
            load_orchestrator(config), initial_agent=config.resolve_default_agent()
        )

        assert "smart-approve" in manager.available_agents
        assert manager.active_profile.name == "smart-approve"

    def test_explicit_smart_approve_selection_bypasses_the_gate(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        """Explicitly selecting smart approve works even when the picker gate is off."""
        manager = AgentManager(
            load_orchestrator(build_config()), initial_agent="smart-approve"
        )

        assert manager.active_profile.name == "smart-approve"

    def test_explicit_smart_approve_stays_in_the_cycle(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        """A --smart-approve start keeps the mode in the cycle after switching away.

        Regression: the mode was active at startup but dropped out of the picker
        once the user cycled off it, because it was hidden by the rollout gate.
        """
        manager = AgentManager(
            load_orchestrator(build_config()), initial_agent="smart-approve"
        )

        assert "smart-approve" in manager.available_agents
        assert "smart-approve" in manager.get_agent_order()

        # Cycle a full loop; smart-approve must be reachable again, not skipped.
        order = manager.get_agent_order()
        current = manager.active_profile
        seen = [current.name]
        for _ in range(len(order)):
            current = manager.next_agent(current)
            seen.append(current.name)

        assert seen.count("smart-approve") >= 2
        assert current.name == "smart-approve"

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
        manager = AgentManager(load_orchestrator(config), initial_agent="ask")
        assert manager.active_profile.name == "ask"

    def test_initial_agent_raises_when_agent_is_disabled(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config(disabled_agents=["ask"])
        with pytest.raises(ValueError, match="disabled_agents") as exc_info:
            AgentManager(load_orchestrator(config), initial_agent="ask")
        message = str(exc_info.value)
        assert "default_agent" not in message
        assert message.startswith("Agent 'ask'")

    def test_explicit_agent_excluded_by_enabled_agents_does_not_blame_default(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config(enabled_agents=["ask"])
        with pytest.raises(ValueError, match="enabled_agents") as exc_info:
            AgentManager(load_orchestrator(config), initial_agent="auto-approve")
        message = str(exc_info.value)
        assert "default_agent" not in message
        assert message.startswith("Agent 'auto-approve'")

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
        config = build_config(enabled_agents=["ask"])
        with pytest.raises(ValueError, match="enabled_agents") as exc_info:
            AgentManager(load_orchestrator(config))
        message = str(exc_info.value)
        assert "accept-edits" in message
        assert "default_agent" in message

    def test_default_agent_excluded_by_disabled_agents_raises_config_contradiction(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config(disabled_agents=["accept-edits"])
        with pytest.raises(ValueError, match="disabled_agents") as exc_info:
            AgentManager(load_orchestrator(config))
        assert "default_agent" in str(exc_info.value)

    def test_disabled_agents_ignored_entirely_when_enabled_agents_set(
        self,
        caplog: pytest.LogCaptureFixture,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config(enabled_agents=["ask"], disabled_agents=["ask"])
        with caplog.at_level("WARNING"):
            manager = AgentManager(load_orchestrator(config), initial_agent="ask")
        assert manager.active_profile.name == "ask"
        assert caplog.text == ""

    def test_removed_plan_default_agent_starts_on_ask(
        self,
        caplog: pytest.LogCaptureFixture,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        config = build_config(default_agent="plan")
        with caplog.at_level("WARNING"):
            manager = AgentManager(
                load_orchestrator(config), initial_agent=config.resolve_default_agent()
            )

        assert manager.active_profile.name == "ask"
        assert "plan" not in manager.available_agents
        assert "default_agent 'plan' is no longer a builtin agent" in caplog.text

    def test_explicit_plan_agent_is_rejected_like_any_unknown_agent(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        with pytest.raises(ValueError, match="Agent 'plan' not found"):
            AgentManager(load_orchestrator(build_config()), initial_agent="plan")

    def test_custom_plan_agent_keeps_serving_as_default_agent(
        self,
        tmp_path: Path,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "plan.toml").write_text(
            'display_name = "My Plan"\n', encoding="utf-8"
        )
        config = build_config(default_agent="plan", agent_paths=[agents_dir])

        manager = AgentManager(
            load_orchestrator(config), initial_agent=config.resolve_default_agent()
        )

        assert manager.active_profile.name == "plan"
        assert manager.active_profile.display_name == "My Plan"

    def test_custom_agent_listing_exit_plan_mode_still_loads(
        self,
        tmp_path: Path,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> None:
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "legacy.toml").write_text(
            'enabled_tools = ["read_file", "exit_plan_mode"]\n'
            'disabled_tools = ["exit_plan_mode"]\n',
            encoding="utf-8",
        )
        config = build_config(agent_paths=[agents_dir])

        manager = AgentManager(load_orchestrator(config), initial_agent="legacy")

        assert manager.active_profile.name == "legacy"

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
