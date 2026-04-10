from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from vibe.core.config import MCPServer, VibeConfig
from vibe.core.mcp.client import MCPClient
from vibe.core.tools.base import ToolError


class RemoteRegistry:
    """Registry for managing remote Vibe instances and their capabilities."""

    def __init__(self, config: VibeConfig):
        self.config = config
        self._clients: dict[str, MCPClient] = {}
        self._capabilities: dict[str, dict[str, Any]] = {}
        self._validate_configuration()

    def _validate_configuration(self) -> None:
        """Validate MCP server configuration."""
        if not self.config.mcp_servers:
            return  # No servers configured is valid (local-only mode)

        seen_names = set()
        for server in self.config.mcp_servers:
            # Check for duplicate names
            if server.name in seen_names:
                raise ValueError(f"Duplicate MCP server name: {server.name}")
            seen_names.add(server.name)

            # Validate required fields based on transport type
            if hasattr(server, 'transport'):
                if server.transport in ['http', 'streamable-http']:
                    if not hasattr(server, 'url') or not server.url:
                        raise ValueError(f"MCP server {server.name} missing URL for HTTP transport")
                elif server.transport == 'stdio':
                    if not hasattr(server, 'command') or not server.command:
                        raise ValueError(f"MCP server {server.name} missing command for STDIO transport")

    def get_remote_names(self) -> list[str]:
        """Get list of configured remote server names."""
        return [server.name for server in self.config.mcp_servers]

    def get_client(self, remote_name: str) -> MCPClient:
        """Get MCP client for a specific remote server."""
        if remote_name not in self._clients:
            server = self._find_server_by_name(remote_name)
            if not server:
                raise ToolError(f"Remote server '{remote_name}' not found")
            self._clients[remote_name] = MCPClient(server)
        return self._clients[remote_name]

    def _find_server_by_name(self, name: str) -> MCPServer | None:
        """Find MCP server configuration by name."""
        for server in self.config.mcp_servers:
            if server.name == name:
                return server
        return None

    async def get_remote_capabilities(self, remote_name: str) -> dict[str, Any]:
        """Get capabilities of a remote server."""
        if remote_name not in self._capabilities:
            try:
                client = self.get_client(remote_name)
                # TODO: Implement actual capabilities discovery via MCP
                # For now, return basic info
                self._capabilities[remote_name] = {
                    "name": remote_name,
                    "available": True,
                    "supports_subagents": True
                }
            except Exception as e:
                self._capabilities[remote_name] = {
                    "name": remote_name,
                    "available": False,
                    "error": str(e)
                }
        return self._capabilities[remote_name]

    async def list_available_remotes(self) -> list[dict[str, Any]]:
        """List all available remote servers with their status."""
        remotes = []
        for server in self.config.mcp_servers:
            capabilities = await self.get_remote_capabilities(server.name)
            remotes.append({
                "name": server.name,
                "available": capabilities.get("available", False),
                "transport": getattr(server, "transport", "unknown")
            })
        return remotes

    async def execute_on_remote(
        self,
        remote_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        fallback_to_local: bool = False
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Execute a tool on a specific remote server with optional fallback."""
        client = self.get_client(remote_name)

        try:
            async for event_data in client.call_tool(tool_name, arguments):
                yield event_data
        except Exception as e:
            error_msg = f"Remote execution failed: {e}"
            yield {"type": "error", "message": error_msg, "fallback_available": fallback_to_local}

            # If fallback is enabled, we could execute locally here
            # For now, just indicate fallback is available
            if fallback_to_local:
                yield {"type": "info", "message": "Fallback to local execution available"}

    def parse_agent_address(self, agent_spec: str) -> tuple[str, str]:
        """Parse agent specification in format 'remote_name:agent_name'.

        Examples:
        - 'main_server:explore' -> ('main_server', 'explore')
        - 'explore' -> ('default', 'explore') if default is configured
        """
        if ":" in agent_spec:
            parts = agent_spec.split(":", 1)
            server_name, agent_name = parts[0], parts[1]

            # Check if the server name is valid, if not use first server as default
            if not any(s.name == server_name for s in self.config.mcp_servers):
                # Unknown server name, use first server as default
                if self.config.mcp_servers:
                    server_name = self.config.mcp_servers[0].name
                else:
                    raise ToolError("No remote servers configured and no default available")

            return server_name, agent_name
        else:
            # Use first configured server as default
            if self.config.mcp_servers:
                return self.config.mcp_servers[0].name, agent_spec
            else:
                raise ToolError("No remote servers configured and no default available")

    def is_remote_agent(self, agent_spec: str) -> bool:
        """Check if agent specification refers to a remote agent."""
        # Check if it contains a colon (remote:agent format)
        # or if it's a known remote server name
        if ":" in agent_spec:
            return True

        # Check if it matches any remote server name directly
        remote_names = self.get_remote_names()
        return agent_spec in remote_names

    def get_configuration_status(self) -> dict[str, Any]:
        """Get current configuration status and validation results."""
        return {
            "servers_configured": len(self.config.mcp_servers),
            "server_names": self.get_remote_names(),
            "validation_passed": True,
            "issues": []
        }