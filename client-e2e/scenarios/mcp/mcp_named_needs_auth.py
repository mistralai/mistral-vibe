"""`/mcp <name>` on a server awaiting OAuth logs in, then reopens on its tools."""

from __future__ import annotations

from typing import Any

from e2e.app_server.mcp import handshake as mcp_handshake, sample_sources, server, tool
from e2e.app_server.scenario import Timeline


def _authenticated() -> list[dict[str, Any]]:
    """The catalog the server publishes once `weather` finished its login."""
    return [
        server("weather", transport="http", tools=[tool("forecast", "Read a forecast")])
        if source["name"] == "weather"
        else source
        for source in sample_sources()
    ]


handshake = mcp_handshake(sample_sources())
handshake["mcp/login"] = {"runtime": {"mcp": {"sources": _authenticated()}}}

timeline: Timeline = ["/mcp weather\r"]

screen_contains = {"rust": ("MCP Server: weather", "forecast")}
