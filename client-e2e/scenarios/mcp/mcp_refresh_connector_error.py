"""A failed connector refresh on `r` still refreshes the MCP servers and shows them."""

from __future__ import annotations

from e2e.app_server.mcp import (
    authenticated_sources,
    handshake as mcp_handshake,
    sample_sources,
)
from e2e.app_server.scenario import Timeline

handshake = mcp_handshake(sample_sources())
handshake["connector_catalog/refresh"] = {
    "error": {"code": "internal_error", "message": "Connectors offline"}
}
handshake["mcp/refresh"] = {"runtime": {"mcp": {"sources": authenticated_sources()}}}
request_methods = frozenset({
    "connector_catalog/refresh",
    "mcp_catalog/refresh",
    "session/ready/wait",
})
screen_contains = ("weather     [http]   1 tool",)
screen_excludes = ("Connectors offline",)
timeline: Timeline = ["/mcp\r", "r"]
