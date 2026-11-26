"""A failed `/mcp login` shows the server's reason and leaves the prompt usable."""

from __future__ import annotations

from e2e.app_server.mcp import handshake as mcp_handshake, sample_sources
from e2e.app_server.scenario import Timeline

handshake = mcp_handshake(sample_sources())
handshake["mcp/login"] = {
    "error": {
        "code": "invalid_params",
        "message": "OAuth login failed: authorization server unreachable",
    }
}
request_methods = frozenset({"mcp_catalog/login"})
screen_contains = (
    "Error: OAuth login failed: authorization server unreachable",
    "MCP auth status",
)
screen_excludes = ("authenticated.",)
timeline: Timeline = ["/mcp login weather\r", "/mcp status\r"]
