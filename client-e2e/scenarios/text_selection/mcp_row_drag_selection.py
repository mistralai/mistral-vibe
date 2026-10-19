"""Dragging across an `/mcp` source row copies its text without opening the source."""

from __future__ import annotations

from e2e.app_server.mcp import connector, handshake as mcp_handshake, server, tool
from e2e.app_server.scenario import Timeline

_SOURCES = [
    server("filesystem", tools=[tool("alpha", "The alpha tool")]),
    connector("github", tools=[tool("create_issue", "Open a GitHub issue")]),
]

handshake = mcp_handshake(_SOURCES)
env = {"SSH_TTY": "/dev/pts/0"}
capture_steps = {1}
expected_clipboard = "filesystem"
screen_contains = ("Local MCP Servers", "Available Connectors")
screen_excludes = ("The alpha tool",)

# Drag across the "filesystem" server name (1-based SGR coordinates).
_DRAG = "\x1b[<0;6;34M\x1b[<32;15;34M\x1b[<0;15;34m"

timeline: Timeline = ["/mcp\r", _DRAG]
