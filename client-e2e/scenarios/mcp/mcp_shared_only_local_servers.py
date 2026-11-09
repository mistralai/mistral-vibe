"""`/mcp` with a shared-only key and no connectors still opens and serves local MCP servers."""

from __future__ import annotations

from e2e.app_server.mcp import handshake as mcp_handshake, server, tool
from e2e.app_server.scenario import Timeline

handshake = mcp_handshake(
    [server("filesystem", tools=[tool("read_file", "Read a file")])],
    private_connectors_hidden=True,
)

_DOWN = "\x1b[B"

# The warning row is not selectable, so Down skips to filesystem and Enter shows its tools.
timeline: Timeline = ["/mcp\r", _DOWN + "\r"]

screen_contains = ("MCP Server: filesystem", "read_file")
