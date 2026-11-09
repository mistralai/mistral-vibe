"""`/mcp` with no sources still opens the panel, with an empty connectors section, after the outage error."""

from __future__ import annotations

from e2e.app_server.mcp import handshake as mcp_handshake
from e2e.app_server.scenario import Timeline

handshake = mcp_handshake(
    [],
    connector_error="Failed to load connectors (HTTP 502).\nServer response: Bad Gateway",
)
screen_contains = (
    "Could not load connectors.",
    "HTTP 502",
    "Bad Gateway",
    "Available Connectors",
    "Connectors could not be loaded.",
)
screen_excludes = ("No MCP servers or connectors configured",)
timeline: Timeline = ["/mcp\r"]
