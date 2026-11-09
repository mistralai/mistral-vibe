"""The topmost visible scrollbar owns a drag until the pointer is released."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.mcp import connector, handshake as mcp_handshake, server, tool
from e2e.app_server.scenario import Timeline

_TOOLS = [
    tool(f"tool_{index:02d}", "A tool exposed by the connector") for index in range(30)
]
_SOURCES = [
    server("filesystem", tools=[tool("read_file", "Read a file")]),
    connector("github_app", tools=_TOOLS),
]

handshake = mcp_handshake(_SOURCES)
capture_startup = False
capture_steps = {1, 4}

_PRESS = "\x1b[<0;117;20M"
_DRAG = "\x1b[<32;117;30M"
_RELEASE = "\x1b[<0;117;30m"
_REPLY = "\n\n".join(f"Discussion line {index:02d}." for index in range(1, 31))

timeline: Timeline = [
    "inspect MCP\r",
    turn_started(),
    user_msg("inspect MCP"),
    assistant_msg(_REPLY),
    turn_completed(),
    "/mcp github_app\r",
    _PRESS,
    _DRAG,
    _RELEASE,
]
