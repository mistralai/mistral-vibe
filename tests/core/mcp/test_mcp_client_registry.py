"""
Test MCP Client and RemoteRegistry functionality using pytest.

This module tests the new MCP client and registry components for distributed workload execution.
"""

from __future__ import annotations

import pytest

from vibe.core.config import MCPHttp, MCPStdio
from vibe.core.mcp.client import MCPClient
from vibe.core.mcp.registry import RemoteRegistry


class TestMCPClient:
    """Test MCPClient creation and functionality."""

    def test_stdio_client_creation(self):
        """Test MCPClient creation with STDIO configuration."""
        config = MCPStdio(
            name="test_stdio",
            transport="stdio",
            command=["python", "-c", "print('hello')"]
        )
        client = MCPClient(config)

        assert client._is_stdio_transport() == True
        assert client.url == ""  # Empty for STDIO
        assert client.headers == {}

    def test_http_client_creation(self):
        """Test MCPClient creation with HTTP configuration."""
        config = MCPHttp(
            name="test_http",
            transport="http",
            url="http://localhost:8080"
        )
        client = MCPClient(config)

        assert client._is_stdio_transport() == False
        assert client.url == "http://localhost:8080"
        assert client.headers == {}

    def test_stdio_parameters_extraction(self):
        """Test STDIO parameter extraction."""
        config = MCPStdio(
            name="test_stdio",
            transport="stdio",
            command=["python", "script.py"],
            args=["--debug"],
            env={"ENV": "test"}
        )
        client = MCPClient(config)
        params = client._get_stdio_parameters()

        assert params.command == "python"
        assert "script.py" in params.args
        assert "--debug" in params.args
        assert params.env == {"ENV": "test"}


class TestRemoteRegistry:
    """Test RemoteRegistry functionality."""

    def test_empty_configuration(self):
        """Test registry with no servers configured."""
        class MockConfig:
            def __init__(self):
                self.mcp_servers = []

        config = MockConfig()
        registry = RemoteRegistry(config)

        status = registry.get_configuration_status()
        assert status["servers_configured"] == 0
        assert status["validation_passed"] == True
        assert registry.get_remote_names() == []

    def test_multiple_servers(self):
        """Test registry with multiple server types."""
        class MockConfig:
            def __init__(self):
                self.mcp_servers = [
                    MCPHttp(name="http_server", transport="http", url="http://localhost:8080"),
                    MCPStdio(name="stdio_server", transport="stdio", command=["python"])
                ]

        config = MockConfig()
        registry = RemoteRegistry(config)

        # Test configuration status
        status = registry.get_configuration_status()
        assert status["servers_configured"] == 2

        # Test remote names
        names = registry.get_remote_names()
        assert len(names) == 2
        assert "http_server" in names
        assert "stdio_server" in names

        # Test server lookup
        http_server = registry._find_server_by_name("http_server")
        stdio_server = registry._find_server_by_name("stdio_server")
        assert http_server is not None
        assert stdio_server is not None

        # Test client creation
        http_client = registry.get_client("http_server")
        stdio_client = registry.get_client("stdio_server")
        assert http_client._is_stdio_transport() == False
        assert stdio_client._is_stdio_transport() == True

    def test_duplicate_server_names(self):
        """Test that duplicate server names are rejected."""
        class MockConfig:
            def __init__(self):
                self.mcp_servers = [
                    MCPHttp(name="dup", transport="http", url="http://server1:8080"),
                    MCPHttp(name="dup", transport="http", url="http://server2:8080")
                ]

        config = MockConfig()

        with pytest.raises(ValueError, match="Duplicate MCP server name"):
            RemoteRegistry(config)

    def test_agent_address_parsing(self):
        """Test agent address parsing with various formats."""
        class MockConfig:
            def __init__(self):
                self.mcp_servers = [
                    MCPHttp(name="server1", transport="http", url="http://server1:8080"),
                    MCPStdio(name="server2", transport="stdio", command=["python"])
                ]

        config = MockConfig()
        registry = RemoteRegistry(config)

        # Test explicit server:agent format
        remote, agent = registry.parse_agent_address("server1:explore")
        assert remote == "server1" and agent == "explore"

        remote, agent = registry.parse_agent_address("server2:research")
        assert remote == "server2" and agent == "research"

        # Test direct server name (should use as agent name)
        remote, agent = registry.parse_agent_address("server1")
        assert remote == "server1" and agent == "server1"

        # Test local agent (should use first server as default)
        remote, agent = registry.parse_agent_address("explore")
        assert remote == "server1" and agent == "explore"

        # Test unknown server (should default to first server)
        remote, agent = registry.parse_agent_address("unknown:test")
        assert remote == "server1" and agent == "test"

    def test_remote_agent_detection(self):
        """Test remote agent detection logic."""
        class MockConfig:
            def __init__(self):
                self.mcp_servers = [
                    MCPHttp(name="remote1", transport="http", url="http://remote1:8080"),
                    MCPStdio(name="remote2", transport="stdio", command=["python"])
                ]

        config = MockConfig()
        registry = RemoteRegistry(config)

        # Remote agents (explicit server:agent)
        assert registry.is_remote_agent("remote1:explore") == True
        assert registry.is_remote_agent("remote2:research") == True

        # Direct server names are remote
        assert registry.is_remote_agent("remote1") == True
        assert registry.is_remote_agent("remote2") == True

        # Local agents (no server prefix)
        assert registry.is_remote_agent("explore") == False
        assert registry.is_remote_agent("research") == False
        assert registry.is_remote_agent("local_agent") == False

        # Unknown but remote format
        assert registry.is_remote_agent("unknown:agent") == True

    def test_error_handling(self):
        """Test error handling in various scenarios."""
        class MockConfig:
            def __init__(self):
                self.mcp_servers = []

        config = MockConfig()
        registry = RemoteRegistry(config)

        # No servers configured
        with pytest.raises(Exception, match="No remote servers configured"):
            registry.parse_agent_address("explore")

        # Non-existent server lookup
        result = registry._find_server_by_name("nonexistent")
        assert result is None

        # Edge cases in agent detection
        assert registry.is_remote_agent("") == False
        assert registry.is_remote_agent(":agent") == True
        assert registry.is_remote_agent("server:") == True
