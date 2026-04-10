"""
Test MCP Server functionality.

This module tests the basic MCP server functionality.
"""

from __future__ import annotations

import pytest

from vibe.core.config import VibeConfig
from vibe.core.mcp.vibe_server import create_vibe_mcp_server


class TestMCPServer:
    """Test MCP Server creation and basic functionality."""

    def test_server_creation(self):
        """Test that VibeMCPServer can be created."""
        # Create a minimal config for testing
        config = VibeConfig.load()
        server = create_vibe_mcp_server(config)
        
        assert server is not None
        assert server.name == "vibe-subagent"
        assert server.version == "1.0.0"
        assert server.instructions == "Vibe subagent execution server"

    def test_server_capabilities(self):
        """Test that server has proper capabilities."""
        config = VibeConfig.load()
        server = create_vibe_mcp_server(config)
        
        capabilities = server.get_capabilities()
        assert capabilities is not None
        assert hasattr(capabilities, 'experimental')
        assert 'vibe' in capabilities.experimental
        assert capabilities.experimental['vibe']['subagent_execution'] == True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
