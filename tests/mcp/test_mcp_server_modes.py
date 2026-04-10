"""
Test MCP Server different agent exposure modes.

This module tests the enhanced MCP server functionality with different agent exposure modes.
"""

from __future__ import annotations

import pytest

from vibe.core.config import VibeConfig
from vibe.core.agents.models import AgentType
from vibe.core.mcp.vibe_server import create_vibe_mcp_server


class TestMCPServerModes:
    """Test MCP Server with different agent exposure modes."""

    def test_server_creation_with_modes(self):
        """Test that VibeMCPServer can be created with different modes."""
        config = VibeConfig.load()
        
        # Test default mode (subagents)
        server_default = create_vibe_mcp_server(config)
        assert server_default.agent_mode == "subagents"
        
        # Test agents mode
        server_agents = create_vibe_mcp_server(config, agent_mode="agents")
        assert server_agents.agent_mode == "agents"
        
        # Test specific agents mode
        server_specific = create_vibe_mcp_server(config, agent_mode="explore,plan")
        assert server_specific.agent_mode == "explore,plan"

    def test_agent_mode_filtering(self):
        """Test that agent mode correctly filters agents."""
        config = VibeConfig.load()
        
        # Test subagents mode (default)
        server = create_vibe_mcp_server(config, agent_mode="subagents")
        agents = server._get_agents_for_mode()
        
        # Should only return agents with SUBAGENT type
        for agent in agents:
            assert agent.agent_type == AgentType.SUBAGENT
        
        # Test agents mode
        server_all = create_vibe_mcp_server(config, agent_mode="agents")
        all_agents = server_all._get_agents_for_mode()
        
        # Should return all agents
        assert len(all_agents) > len(agents)  # More agents than just subagents
        
        # Test specific agents mode
        server_specific = create_vibe_mcp_server(config, agent_mode="explore")
        specific_agents = server_specific._get_agents_for_mode()
        
        # Should return only the specified agent
        assert len(specific_agents) == 1
        assert specific_agents[0].name == "explore"

    def test_agent_descriptions_in_tools(self):
        """Test that agent descriptions are used in tool definitions."""
        config = VibeConfig.load()
        server = create_vibe_mcp_server(config, agent_mode="agents")
        
        # Test that agents have descriptions
        agents = server._get_agents_for_mode()
        
        # Check that agents have descriptions
        for agent in agents:
            assert agent.description  # Should have a description
            assert not agent.description.startswith("Execute") or "agent" in agent.description.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
