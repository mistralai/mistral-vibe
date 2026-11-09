"""A no-auth connector reads `not connected`; Enter connects it and lists its tools."""

from __future__ import annotations

from typing import Any

from e2e.app_server.mcp import connector, handshake as mcp_handshake, server, tool
from e2e.app_server.scenario import Timeline


def _sources(deepwiki: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        server("filesystem", tools=[tool("read_file", "Read a file")]),
        connector("github", tools=[tool("create_issue", "Open a GitHub issue")]),
        deepwiki,
    ]


_PENDING = _sources(
    connector("deepwiki", display_name="DeepWiki", status="needs_connect")
)
_CONNECTED = _sources(
    connector(
        "deepwiki",
        display_name="DeepWiki",
        tools=[tool("ask_question", "Ask a question about a repository")],
    )
)

handshake = mcp_handshake(_PENDING)
handshake["connector_catalog/connect"] = {"runtime": {"mcp": {"sources": _CONNECTED}}}
request_methods = frozenset({"connector_catalog/connect"})
screen_contains = ("Connector: DeepWiki", "ask_question")
screen_excludes = ("error - try refreshing",)

_DOWN = "\x1b[B"

# Open the browser, walk past filesystem and GitHub to DeepWiki, then connect it.
timeline: Timeline = ["/mcp\r", _DOWN + _DOWN, "\r"]
