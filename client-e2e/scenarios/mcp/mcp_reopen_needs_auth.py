"""A login that leaves the server unauthenticated reopens on the list instead of looping."""

from __future__ import annotations

from e2e.app_server.mcp import handshake as mcp_handshake, sample_sources
from e2e.app_server.scenario import Timeline

handshake = mcp_handshake(sample_sources())

timeline: Timeline = ["/mcp weather\r"]

screen_contains = {"rust": ("Local MCP Servers", "needs auth")}
