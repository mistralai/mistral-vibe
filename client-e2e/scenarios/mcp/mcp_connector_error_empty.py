"""`/mcp` with no sources shows the connector outage instead of the empty-catalog message."""

from __future__ import annotations

from e2e.app_server.mcp import handshake as mcp_handshake
from e2e.app_server.scenario import Timeline

handshake = mcp_handshake(
    [],
    connector_error="Failed to load connectors (HTTP 502).\nServer response: Bad Gateway",
)
screen_contains = ("Could not load connectors.", "HTTP 502", "Bad Gateway")
screen_excludes = (
    "No MCP servers or connectors configured",
    "MCP and connectors opened",
)
timeline: Timeline = ["/mcp\r"]
