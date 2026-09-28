"""Boolean choices leave room for every layer and all save targets."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

handshake = {
    "config/fields/read": {
        "fields": [
            {
                "name": "enable_telemetry",
                "kind": "bool",
                "value": True,
                "description": "Allow anonymous usage telemetry.",
                "path": "/enable_telemetry",
                "popular": True,
                "enumChoices": [],
                "valueLabels": {},
                "layerValues": [
                    {"layer": "overrides", "value": True},
                    {"layer": "project-toml", "value": False},
                    {"layer": "user-toml", "value": True},
                    {"layer": "default", "value": False},
                ],
            }
        ],
        "targets": ["user-toml", "project-toml", "overrides"],
    }
}

handshake["config/fields/read"]["fields"].append({
    "name": "log_level",
    "kind": "str",
    "value": "WARNING",
    "description": "",
    "path": "/log_level",
    "popular": False,
    "enumChoices": [],
    "valueLabels": {},
    "layerValues": [{"layer": "default", "value": "WARNING"}],
})

capture_startup = False
screen_contains = {
    "rust": ("True", "False", "defaults", "user config", "project config")
}
timeline: Timeline = ["/config\r", "\r", "\x1b[B", "\t"]
