from __future__ import annotations

import pytest
from tests.conftest import build_test_vibe_config
from vibe.core.agents.manager import AgentManager
from vibe.core.agents.models import BUILTIN_AGENTS, PLAN


class TestPlanAgentTools:
    def test_config_plan_agent_tools_default(self):
        """Test that config has default plan agent tools."""
        config = build_test_vibe_config()
        expected_tools = ["grep", "read_file", "todo", "ask_user_question", "task"]
        assert hasattr(config, 'plan_agent_tools'), "Config should have plan_agent_tools attribute"
        assert config.plan_agent_tools == expected_tools

    def test_config_plan_agent_tools_custom(self):
        """Test that config can load custom plan agent tools."""
        config = build_test_vibe_config(plan_agent_tools=["grep", "read_file", "web_search"])
        assert config.plan_agent_tools == ["grep", "read_file", "web_search"]

    def test_plan_agent_uses_config_tools(self):
        """Test that PLAN agent uses tools from config."""
        config = build_test_vibe_config(plan_agent_tools=["grep", "read_file", "web_search"])
        plan_agent = BUILTIN_AGENTS["plan"]

        # Apply agent overrides to config
        agent_config = plan_agent.apply_to_config(config)

        assert agent_config.enabled_tools == ["grep", "read_file", "web_search"]
        assert agent_config.auto_approve is True

    def test_agent_manager_respects_plan_tools(self):
        """Test that AgentManager correctly applies plan agent tool configuration."""
        config = build_test_vibe_config(plan_agent_tools=["grep", "read_file"])
        manager = AgentManager(lambda: config)

        # Switch to plan agent
        manager.switch_profile("plan")
        active_config = manager.config

        assert active_config.enabled_tools == ["grep", "read_file"]
        assert active_config.auto_approve is True