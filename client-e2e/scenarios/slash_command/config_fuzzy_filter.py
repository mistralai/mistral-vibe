"""Config search matches descriptions and case-insensitive abbreviations."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

handshake = {
    "config/fields/read": {
        "fields": [
            {
                "name": name,
                "path": f"/{name}",
                "kind": "int",
                "value": 42,
                "description": description,
                "popular": popular,
                "enumChoices": [],
                "valueLabels": {},
                "layerValues": [{"layer": "default", "value": 42}],
            }
            for name, description, popular in [
                ("max_tokens", "Token budget.", True),
                ("api_timeout", "Network timeout.", False),
                ("color_depth", "Display colors.", False),
            ]
        ],
        "targets": ["user-toml", "overrides"],
    }
}

capture_startup = False
screen_contains = {client: ("api_timeout",) for client in ("rust", "python")}
screen_excludes = {
    client: ("max_tokens", "color_depth") for client in ("rust", "python")
}
timeline: Timeline = ["/config\r", "network", "\x7f" * 7 + "APTO"]
