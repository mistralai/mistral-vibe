"""Moving up inside a long JSON draft keeps the view until the caret leaves it."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

value = {f"k{index:02}": f"v{index:02}" for index in range(20)}

handshake = {
    "config/fields/read": {
        "fields": [
            {
                "name": "mcp_servers",
                "kind": "complex",
                "value": value,
                "description": "MCP server definitions.",
                "path": "/mcp_servers",
                "popular": True,
                "enumChoices": [],
                "valueLabels": {},
                "layerValues": [
                    {"layer": "user-toml", "value": value},
                    {"layer": "default", "value": {}},
                ],
            }
        ],
        "targets": ["user-toml", "project-toml", "overrides"],
    }
}

capture_startup = False
screen_contains = {"rust": ('"v19"', '"v16"')}
timeline: Timeline = ["/config\r", "\r", "\x1b[A" * 3]
