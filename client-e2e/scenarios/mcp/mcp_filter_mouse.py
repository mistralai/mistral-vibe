"""Mouse focus and filtered clicks open the correct source, and reopening clears search."""

from __future__ import annotations

from e2e.app_server.mcp import handshake as mcp_handshake, sample_sources
from e2e.app_server.scenario import Timeline

handshake = mcp_handshake(sample_sources())
request_methods = {"telemetry/record"}


def _click(column: int, row: int) -> str:
    return f"\x1b[<0;{column};{row}M\x1b[<0;{column};{row}m"


timeline: Timeline = [
    "/mcp\r",
    _click(20, 28),
    "GH",
    _click(20, 37),
    "\x7f",
    "/",
    "\x1b",
    "\x1b",
    "/mcp\r",
]

screen_contains = {"rust": ("Local MCP Servers", "filesystem", "github", "gmail")}
screen_excludes = {"rust": ("GH", "Connector: github")}
