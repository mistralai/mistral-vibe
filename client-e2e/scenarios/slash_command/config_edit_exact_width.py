"""Keep the caret visible when a config value exactly fills its input row."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, resize

value = "123456789012345678901234567"
handshake = {
    "config/fields/read": {
        "fields": [
            {
                "name": "endpoint",
                "kind": "str",
                "value": value,
                "description": "",
                "path": "/endpoint",
                "popular": True,
                "enumChoices": [],
                "valueLabels": {},
                "layerValues": [{"layer": "default", "value": value}],
            }
        ],
        "targets": ["user-toml", "overrides"],
    }
}

capture_startup = False
screen_contains = {"rust": (value,)}
timeline: Timeline = [resize(40, 80), "/config\r", "\r", "\x1b[H", "\x1b[F"]
