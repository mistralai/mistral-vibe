"""`/mcp add` forwards alias, repeated scopes, http transport and the insecure-http opt-in."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

handshake = {"mcp/add": {"name": "docs", "url": "http://192.168.0.8:3002/mcp"}}
request_methods = frozenset({"mcp_catalog/add", "mcp_catalog/login"})
screen_contains = ("Added MCP server docs.", "/mcp login docs")
timeline: Timeline = [
    "/mcp add http://192.168.0.8:3002/mcp --name docs --scope read --scope write"
    " --transport http --allow-insecure-http --no-login\r"
]
