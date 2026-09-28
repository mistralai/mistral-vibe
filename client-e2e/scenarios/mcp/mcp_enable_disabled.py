"""Enabling a tool of a disabled connector enables the connector itself."""

from __future__ import annotations

from e2e.app_server.mcp import connector, handshake as mcp_handshake, server, tool
from e2e.app_server.scenario import Timeline

_SOURCES = [
    server("filesystem", tools=[tool("read_file", "Read a file")]),
    connector(
        "github_app",
        status="disabled",
        tools=[
            tool("create_issue", "Open a GitHub issue", enabled=False),
            tool("list_issues", "List GitHub issues", enabled=False),
        ],
    ),
]
# The server persists a connector entry for the tool, which also enables it.
_TOGGLED = [
    connector(
        "github_app",
        status="connected",
        tools=[
            tool("create_issue", "Open a GitHub issue"),
            tool("list_issues", "List GitHub issues", enabled=False),
        ],
    )
    if source["name"] == "github_app"
    else source
    for source in _SOURCES
]

handshake = mcp_handshake(_SOURCES, toggled=_TOGGLED)

timeline: Timeline = ["/mcp github_app\r", "e", "\x7f"]
