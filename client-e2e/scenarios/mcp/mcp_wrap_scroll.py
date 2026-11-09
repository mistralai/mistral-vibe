"""Wrapping past either end scrolls the whole list into view, headers included."""

from __future__ import annotations

from e2e.app_server.mcp import connector, handshake as mcp_handshake, server, tool
from e2e.app_server.scenario import Timeline

_SOURCES = [
    *(
        server(f"server_{index:02d}", tools=[tool("read_file", "Read a file")])
        for index in range(20)
    ),
    connector("github_app", tools=[tool("list_issues", "List issues")]),
]

handshake = mcp_handshake(_SOURCES)

_UP = "\x1b[A"
_DOWN = "\x1b[B"

timeline: Timeline = ["/mcp\r", _UP, _DOWN]
