"""`--continue` on an unready runtime waits for it, then shows its notices, model and context."""

from __future__ import annotations

from e2e.app_server.mcp import handshake as mcp_handshake, server
from e2e.app_server.scenario import Timeline

handshake = mcp_handshake([server("weather", transport="http", status="needs_auth")])
handshake["runtime/read"]["ready"] = False
handshake["runtime/read"]["runtime"].update({
    "stats": {"contextTokens": 50_000},
    "contextWindow": 200_000,
    "config": {
        "activeModel": {
            "name": "devstral-small",
            "alias": "devstral-small",
            "displayName": "devstral-small",
            "thinking": "off",
        }
    },
})
# The deferred init is awaited before the notices and the runtime they read.
request_methods = {"session/ready/wait"}
client_args = ("--continue",)
timeline: Timeline = []

screen_contains = (
    "Saved session restored.",
    "MCP server 'weather' needs OAuth authentication.",
    "devstral-small",
    "50k/200k tokens (25%)",
)
