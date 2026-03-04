from __future__ import annotations

import pytest

from tests.conftest import build_test_vibe_config
from vibe.core.agents.manager import AgentManager
from vibe.core.agents.models import BUILTIN_AGENTS


class TestPlanAgentIntegration:
    def test_plan_agent_integration_with_tools(self):
        """Integration test that verifies the complete plan agent tool configuration flow."""
        # Test with custom tools
        config = build_test_vibe_config(plan_agent_tools=["grep", "read_file", "web_search"])
        manager = AgentManager(lambda: config)
        
        # Start with default agent
        default_config = manager.config
        assert default_config.enabled_tools == []  # Default agent has no specific tools enabled
        
        # Switch to plan agent
        manager.switch_profile("plan")
        plan_config = manager.config
        
        # Verify plan agent uses the custom tools
        assert plan_config.enabled_tools == ["grep", "read_file", "web_search"]
        assert plan_config.auto_approve is True
        
        # Verify the plan agent profile is correct
        plan_agent = BUILTIN_AGENTS["plan"]
        assert plan_agent.name == "plan"
        assert plan_agent.safety == "safe"
        
    def test_plan_agent_default_behavior(self):
        """Test that plan agent works with default tools when none are specified."""
        # Test with default configuration
        config = build_test_vibe_config()
        manager = AgentManager(lambda: config)
        
        # Switch to plan agent
        manager.switch_profile("plan")
        plan_config = manager.config
        
        # Verify default tools are used
        expected_default = ["grep", "read_file", "todo", "ask_user_question", "task"]
        assert plan_config.enabled_tools == expected_default
        assert plan_config.auto_approve is True

    def test_plan_agent_tool_config_isolation(self):
        """Test that plan agent tool configuration doesn't affect other agents."""
        config = build_test_vibe_config(plan_agent_tools=["grep", "read_file"])
        manager = AgentManager(lambda: config)
        
        # Check default agent (should not be affected)
        default_config = manager.config
        assert default_config.enabled_tools == []
        
        # Switch to plan agent
        manager.switch_profile("plan")
        plan_config = manager.config
        assert plan_config.enabled_tools == ["grep", "read_file"]
        
        # Switch back to default agent
        manager.switch_profile("default")
        back_to_default = manager.config
        assert back_to_default.enabled_tools == []  # Should be back to default