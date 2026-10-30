"""`/mcp` without configured sources opens the browser with both sections and their empty notes."""

from __future__ import annotations

from e2e.app_server.mcp import handshake as mcp_handshake
from e2e.app_server.scenario import Timeline

handshake = mcp_handshake([])

timeline: Timeline = ["/mcp\r"]

screen_contains = ("No local MCP servers configured.", "No connectors available.")
