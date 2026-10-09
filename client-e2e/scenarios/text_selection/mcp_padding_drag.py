"""A drag from an `/mcp` row's padding selects nothing and never opens the source."""

from __future__ import annotations

from e2e.app_server.mcp import connector, handshake as mcp_handshake, server, tool
from e2e.app_server.scenario import Timeline

_SOURCES = [
    server("filesystem", tools=[tool("alpha", "The alpha tool")]),
    connector("github", tools=[tool("create_issue", "Open a GitHub issue")]),
]

handshake = mcp_handshake(_SOURCES)
capture_steps = {1}
screen_contains = {"rust": ("Local MCP Servers", "Available Connectors")}
screen_excludes = {"rust": ("The alpha tool",)}

# Press on the padding column left of "filesystem" and drag along the row (1-based SGR coordinates).
_DRAG = "\x1b[<0;2;34M\x1b[<32;20;34M\x1b[<0;20;34m"

timeline: Timeline = ["/mcp\r", _DRAG]
