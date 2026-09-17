"""The wheel scrolls the tool list, and a click then selects the row under it."""

from __future__ import annotations

from e2e.app_server.mcp import connector, handshake as mcp_handshake, server, tool
from e2e.app_server.scenario import Timeline

_TOOLS = [
    tool(
        f"tool_{index:02d}",
        "A tool with a description long enough to wrap onto a second line",
    )
    for index in range(30)
]
_SOURCES = [
    server("filesystem", tools=[tool("read_file", "Read a file")]),
    connector("github_app", tools=_TOOLS),
]
_TOGGLED = [
    {
        **source,
        "tools": [
            {**item, "enabled": item["name"] != "tool_03"} for item in source["tools"]
        ],
    }
    if source["name"] == "github_app"
    else source
    for source in _SOURCES
]

handshake = mcp_handshake(_SOURCES, toggled=_TOGGLED)

_WHEEL_DOWN = "\x1b[<65;60;30M"
_CLICK_TOOL_03 = "\x1b[<0;20;34M\x1b[<0;20;34m"

timeline: Timeline = ["/mcp github_app\r", _WHEEL_DOWN * 3, _CLICK_TOOL_03, "d"]
