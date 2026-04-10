"""
Vibe MCP Server package.

This package provides the MCP (Mistral Communication Protocol) server functionality
for Vibe, enabling remote subagent execution and distributed workload processing.
"""

from vibe.mcp.entrypoint import run_mcp_server

__all__ = ["run_mcp_server"]
