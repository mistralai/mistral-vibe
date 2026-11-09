"""A failed connect returns to the list, still `not connected`, and shows why."""

from __future__ import annotations

from e2e.app_server.mcp import connector, handshake as mcp_handshake, server, tool
from e2e.app_server.scenario import Timeline

handshake = mcp_handshake([
    server("filesystem", tools=[tool("read_file", "Read a file")]),
    connector("deepwiki", display_name="DeepWiki", status="needs_connect"),
])
handshake["connector_catalog/connect"] = {
    "error": {
        "code": "internal_error",
        "message": (
            "Could not connect DeepWiki (HTTP 503). Try again, "
            "or enable it in the Mistral dashboard."
        ),
    }
}
request_methods = frozenset({"connector_catalog/connect"})
screen_contains = ("Could not connect DeepWiki (HTTP 503)", "not connected")

_DOWN = "\x1b[B"

timeline: Timeline = ["/mcp\r", _DOWN, "\r"]
