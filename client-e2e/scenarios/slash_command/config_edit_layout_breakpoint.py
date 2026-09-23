"""Keep long-description editors usable across layout breakpoints."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, resize

handshake = {
    "config/fields/read": {
        "fields": [
            {
                "name": "vision_model",
                "kind": "complex",
                "value": None,
                "description": (
                    "Vision-capable model that describes attached images for an active "
                    "model that cannot see them. Only needed to override the default, "
                    "which is any vision-capable model on the active model's own provider; "
                    "set this to reach a different provider. Requires --experimental-harness."
                ),
                "path": "/vision_model",
                "popular": True,
                "enumChoices": [],
                "valueLabels": {},
                "layerValues": [{"layer": "default", "value": None}],
            }
        ],
        "targets": ["user-toml", "overrides"],
    }
}

capture_startup = False
screen_contains = {"rust": ("Ctrl+S Save", "Vision-capable model")}
screen_excludes = {"rust": ("Enlarge terminal",)}
timeline: Timeline = [resize(24, 73), "/config\r", "\r", resize(24, 74)]
