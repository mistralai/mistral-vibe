"""Show formatted JSON and preserve an invalid multiline draft with its error."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

handshake = {
    "config/fields/read": {
        "fields": [
            {
                "name": "tools",
                "kind": "complex",
                "value": {"bash": {"permission": "ask"}},
                "description": "Tool configuration.",
                "path": "/tools",
                "popular": True,
                "enumChoices": [],
                "valueLabels": {},
                "layerValues": [
                    {"layer": "user-toml", "value": {"bash": {"permission": "ask"}}},
                    {"layer": "default", "value": {}},
                ],
            }
        ],
        "targets": ["user-toml", "project-toml", "overrides"],
    }
}

capture_startup = False
screen_contains = {
    "rust": ('"permission": "ask"', "Expected valid JSON", "Edit as JSON.")
}
timeline: Timeline = ["/config\r", "\r", "x\x13"]
