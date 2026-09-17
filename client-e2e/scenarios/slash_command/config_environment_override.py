"""Environment-pinned config values cannot be reset by the client."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

handshake = {
    "config/fields/read": {
        "fields": [
            {
                "name": "theme",
                "kind": "str",
                "description": "",
                "value": "ansi-dark",
                "path": "/theme",
                "popular": True,
                "enumChoices": [],
                "valueLabels": {},
                "layerValues": [
                    {"layer": "environment", "value": "ansi-dark"},
                    {"layer": "user-toml", "value": "textual-dark"},
                    {"layer": "default", "value": "auto"},
                ],
            },
            {
                "name": "enable_otel",
                "kind": "bool",
                "description": "",
                "value": False,
                "path": "/enable_otel",
                "popular": False,
                "enumChoices": [],
                "valueLabels": {},
                "layerValues": [{"layer": "default", "value": False}],
            },
        ],
        "targets": ["user-toml", "overrides"],
    }
}

capture_startup = False
screen_contains = {
    "python": ("'theme' is pinned by env; nothing to clear.",),
    "rust": ("'theme' is pinned by env; nothing to clear.",),
}
timeline: Timeline = ["/config\r", "\x12"]
