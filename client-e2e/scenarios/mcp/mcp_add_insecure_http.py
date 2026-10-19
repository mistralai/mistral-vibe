"""A plaintext LAN `/mcp add` without the opt-in shows the server's --allow-insecure-http advice."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

MESSAGE = (
    "MCP server URL must use https unless it points to localhost. "
    "Pass --allow-insecure-http to use a plaintext http:// URL anyway."
)
handshake = {"mcp/add": {"error": {"code": "invalid_params", "message": MESSAGE}}}
request_methods = frozenset({"mcp_catalog/add", "mcp_catalog/login"})
screen_contains = ("Error: MCP server URL must use https", "--allow-insecure-http")
screen_excludes = ("Added OAuth MCP server",)
timeline: Timeline = [
    "/mcp add http://192.168.0.8:3002/mcp --transport http --no-login\r"
]
