"""Long tool descriptions wrap inside the box and scroll line by line."""

from __future__ import annotations

from e2e.app_server.mcp import connector, handshake as mcp_handshake, server, tool
from e2e.app_server.scenario import Timeline

_TOOLS = [
    tool(
        f"tool_{index:02d}",
        "Add review comment to the requester's latest pending pull request review, "
        "with a very long description that must wrap at the box edge",
        enabled=index % 3 != 0,
    )
    for index in range(30)
]

handshake = mcp_handshake([
    server("filesystem", tools=[tool("read_file", "Read a file")]),
    connector("github_app", tools=_TOOLS),
])

_DOWN = "\x1b[B"

timeline: Timeline = ["/mcp github_app\r", _DOWN * 5, _DOWN * 8, "d", "\x7f"]
