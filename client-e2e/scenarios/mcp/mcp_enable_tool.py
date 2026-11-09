"""`e` enables a connector's tool when the browser was opened by name."""

from __future__ import annotations

from e2e.app_server.mcp import connector, handshake as mcp_handshake, server, tool
from e2e.app_server.scenario import Timeline

_TOOLS = [
    tool("create_issue", "Open a GitHub issue", enabled=False),
    tool("list_issues", "List GitHub issues"),
]
_SOURCES = [
    server("filesystem", tools=[tool("read_file", "Read a file")]),
    connector("github_app", tools=_TOOLS),
]
_TOGGLED = [
    {**source, "tools": [{**item, "enabled": True} for item in source["tools"]]}
    if source["name"] == "github_app"
    else source
    for source in _SOURCES
]

handshake = mcp_handshake(_SOURCES, toggled=_TOGGLED)

timeline: Timeline = ["/mcp github_app\r", "e"]
