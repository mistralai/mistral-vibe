from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import build_test_vibe_config
from vibe.core.agents.manager import AgentManager
from vibe.core.agents.models import (
    BUILTIN_AGENTS,
    EXPLORE,
    AgentProfile,
    AgentSafety,
    AgentType,
)


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
    def manager(self) -> AgentManager:
        config = build_test_vibe_config(
            include_project_context=False, include_prompt_detail=False
        )
        return AgentManager(lambda: config)

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

    def test_initial_agent_rejects_subagent(self) -> None:
        config = build_test_vibe_config(
            include_project_context=False, include_prompt_detail=False
        )
        with pytest.raises(ValueError, match="cannot be used as the primary agent"):
            AgentManager(lambda: config, initial_agent="explore")

    def test_initial_agent_accepts_subagent_when_allowed(self) -> None:
        config = build_test_vibe_config(
            include_project_context=False, include_prompt_detail=False
        )
        manager = AgentManager(
            lambda: config, initial_agent="explore", allow_subagent=True
        )
        assert manager.active_profile.name == "explore"

    def test_initial_agent_accepts_agent_type(self) -> None:
        config = build_test_vibe_config(
            include_project_context=False, include_prompt_detail=False
        )
        manager = AgentManager(lambda: config, initial_agent="plan")
        assert manager.active_profile.name == "plan"


class TestAgentProfileFromMd:
    def test_parse_md_agent(self, tmp_path: Path) -> None:
        md = tmp_path / "my-agent.md"
        md.write_text(
            '---\nname: "My Agent"\ndescription: "A test agent"\ntools:\n  - bash\n  - read_file\n---\n\n# Instructions\n\nDo things.',
            encoding="utf-8",
        )
        agent = AgentProfile.from_md(md)
        assert agent.name == "my-agent"
        assert agent.display_name == "My Agent"
        assert agent.description == "A test agent"
        assert agent.overrides["enabled_tools"] == ["bash", "read_file"]
        assert "# Instructions" in agent.overrides["custom_system_prompt"]

    def test_parse_md_agent_no_tools(self, tmp_path: Path) -> None:
        md = tmp_path / "simple.md"
        md.write_text(
            '---\ndescription: "Simple"\n---\n\nJust instructions.', encoding="utf-8"
        )
        agent = AgentProfile.from_md(md)
        assert agent.name == "simple"
        assert "enabled_tools" not in agent.overrides
        assert agent.overrides["custom_system_prompt"] == "Just instructions."

    def test_parse_md_agent_comma_separated_tools(self, tmp_path: Path) -> None:
        md = tmp_path / "csv-tools.md"
        md.write_text("---\ntools: Glob, Grep, Read\n---\n\nBody.", encoding="utf-8")
        agent = AgentProfile.from_md(md)
        assert agent.overrides["enabled_tools"] == ["Glob", "Grep", "Read"]

    def test_manager_discovers_md_agents(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "review.md").write_text(
            '---\nname: "Reviewer"\ndescription: "Reviews code"\n---\n\nReview instructions.',
            encoding="utf-8",
        )
        config = build_test_vibe_config(
            include_project_context=False,
            include_prompt_detail=False,
            agent_paths=[agents_dir],
        )
        manager = AgentManager(lambda: config)
        assert "review" in manager.available_agents
        assert manager.available_agents["review"].display_name == "Reviewer"
