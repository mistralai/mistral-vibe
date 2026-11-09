"""`/mcp login <alias>` runs the server OAuth login, even when a connector shares the alias."""

from __future__ import annotations

from e2e.app_server.mcp import connector, handshake as mcp_handshake, sample_sources
from e2e.app_server.scenario import Timeline

handshake = mcp_handshake([
    *sample_sources(),
    connector("weather", status="needs_auth"),
])
request_methods = frozenset({"mcp_catalog/login", "connectors/auth/read"})
screen_contains = ("MCP server weather authenticated.",)
screen_excludes = ("MCP and connectors opened", "Error")
timeline: Timeline = ["/mcp login weather\r"]
