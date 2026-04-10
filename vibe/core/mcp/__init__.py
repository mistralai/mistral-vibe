"""
MCP (Mistral Communication Protocol) integration for Vibe.

This package provides MCP server and client functionality for Vibe,
enabling remote subagent execution and distributed workload processing.
"""

from vibe.core.mcp.client import MCPClient
from vibe.core.mcp.registry import RemoteRegistry
from vibe.core.mcp.vibe_server import VibeMCPServer, create_vibe_mcp_server

__all__ = ["MCPClient", "RemoteRegistry", "VibeMCPServer", "create_vibe_mcp_server"]
