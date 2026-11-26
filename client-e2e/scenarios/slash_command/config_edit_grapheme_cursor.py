"""Keep a config editor's block cursor on one complete grapheme."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

value = "👩‍💻x"
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

timeline: Timeline = ["/config\r", "\r", "\x1b[H", take_snapshot()]
