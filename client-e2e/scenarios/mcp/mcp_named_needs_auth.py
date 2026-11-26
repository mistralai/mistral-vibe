"""`/mcp <name>` on a server awaiting OAuth logs in, then reopens on its tools."""

from __future__ import annotations

from e2e.app_server.mcp import (
    authenticated_sources,
    handshake as mcp_handshake,
    sample_sources,
)
from e2e.app_server.scenario import Timeline

handshake = mcp_handshake(sample_sources())
handshake["mcp/login"] = {"runtime": {"mcp": {"sources": authenticated_sources()}}}

timeline: Timeline = ["/mcp weather\r"]

screen_contains = ("MCP Server: weather", "forecast")
