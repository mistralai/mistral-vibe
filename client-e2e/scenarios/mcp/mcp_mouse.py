"""Clicking a source opens its tools, and clicking a tool highlights it."""

from __future__ import annotations

from e2e.app_server.mcp import connector, handshake as mcp_handshake, server, tool
from e2e.app_server.scenario import Timeline

_NAMES = ("alpha", "beta", "gamma", "delta")
_TOOLS = [tool(name, f"The {name} tool") for name in _NAMES]
_SOURCES = [
    server("filesystem", tools=_TOOLS),
    connector("github", tools=[tool("create_issue", "Open a GitHub issue")]),
]
_TOGGLED = [
    {
        **source,
        "tools": [
            {**item, "enabled": item["name"] != "gamma"} for item in source["tools"]
        ],
    }
    if source["name"] == "filesystem"
    else source
    for source in _SOURCES
]

handshake = mcp_handshake(_SOURCES, toggled=_TOGGLED)


def _click(column: int, row: int) -> str:
    """SGR press and release of the left button at a 1-based cell."""
    return f"\x1b[<0;{column};{row}M\x1b[<0;{column};{row}m"


timeline: Timeline = [
    "/mcp\r",
    _click(20, 33),  # the "Local MCP Servers" header, which is not selectable
    _click(20, 34),  # the "filesystem" server row opens its tools
    _click(20, 37),  # the "gamma" tool row
    "d",
]
