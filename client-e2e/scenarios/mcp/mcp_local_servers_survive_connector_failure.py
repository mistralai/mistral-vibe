"""`/mcp` keeps local MCP servers usable when the connector service rejects a shared-only key."""

from __future__ import annotations

from e2e.app_server.mcp import handshake as mcp_handshake, server, tool
from e2e.app_server.scenario import Timeline

handshake = mcp_handshake(
    [
        server("filesystem", tools=[tool("read_file", "Read a file")]),
        server("weather", transport="http", tools=[tool("forecast")]),
    ],
    connector_error="Failed to load connectors (HTTP 401).",
)
handshake["connector_catalog/read"] = {
    "error": {
        "code": "internal_error",
        "message": "Failed to load connectors (HTTP 401).",
    }
}

# Open the browser, then Enter on filesystem shows its tools: local servers stay usable.
timeline: Timeline = ["/mcp\r", "\r"]

screen_contains = ("MCP Server: filesystem", "read_file")
