"""Edit multiple list lines and retain the caret after navigation and paste."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, resize

handshake = {
    "config/fields/read": {
        "fields": [
            {
                "name": "disabled_tools",
                "kind": "list",
                "value": ["bash", "read_file", "grep"],
                "description": "Disable one tool per line.",
                "path": "/disabled_tools",
                "popular": True,
                "enumChoices": [],
                "valueLabels": {},
                "layerValues": [
                    {"layer": "user-toml", "value": ["bash", "read_file", "grep"]},
                    {"layer": "default", "value": []},
                ],
            }
        ],
        "targets": ["user-toml", "project-toml", "overrides"],
    }
}

capture_startup = False
screen_contains = {
    "rust": ("bash", "Read_file", "grep", "écrire", "One item per line.")
}
timeline: Timeline = [
    "/config\r",
    "\r",
    "\x1b[A\x1b[H\x1b[3~R",
    "\x1b[1;5F\r\x1b[200~écrire\x1b[201~",
    resize(24, 80),
    resize(40, 120),
]
