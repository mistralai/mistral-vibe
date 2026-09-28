"""Search owns editing shortcuts and paste, while refresh and tool navigation keep the filter."""

from __future__ import annotations

from e2e.app_server.mcp import handshake as mcp_handshake, sample_sources
from e2e.app_server.scenario import Timeline

handshake = mcp_handshake(sample_sources())
request_methods = {"telemetry/record", "mcp/refresh", "mcp/toggle", "connectors/toggle"}

timeline: Timeline = [
    "/mcp\r",
    "\x1b[A",
    "derjk",
    "\x15",
    "\x1b[B",
    "\x1b[D",
    "\x1b[200~fsé\n\x1b[201~",
    "\x7f",
    "yx",
    "\x1b[D\x1b[3~",
    "\r",
    "r",
    "\r",
]

screen_contains = {
    "rust": ("MCP Server: filesystem", "read_file", "write_file", "(disabled)")
}
screen_excludes = {"rust": ("Search servers and connectors", "Available Connectors")}
