"""Filtering a scrolled source list resets selection and restores the first row when cleared."""

from __future__ import annotations

from e2e.app_server.mcp import handshake as mcp_handshake, server, tool
from e2e.app_server.scenario import Timeline

handshake = mcp_handshake([
    server(f"entry-{index:02}", tools=[tool("read_file")]) for index in range(25)
])
request_methods = {"telemetry/record"}

timeline: Timeline = ["/mcp\r", "\x1b[B" * 24, "/entry-24", "\x15", "\x1b[B\r"]

screen_contains = {"rust": ("MCP Server: entry-00", "read_file")}
screen_excludes = {"rust": ("entry-24", "No matching MCP servers or connectors")}
