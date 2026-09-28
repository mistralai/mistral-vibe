"""Shared config editor scenario fixtures."""

from __future__ import annotations

AUTO_COMPACT_HANDSHAKE = {
    "config/fields/read": {
        "fields": [
            {
                "name": "auto_compact_threshold",
                "kind": "int",
                "value": 256000,
                "description": "Token count before automatic compaction.",
                "path": "/auto_compact_threshold",
                "popular": True,
                "enumChoices": [],
                "valueLabels": {},
                "layerValues": [
                    {"layer": "overrides", "value": 256000},
                    {"layer": "environment", "value": 240000},
                    {"layer": "project-toml", "value": 220000},
                    {"layer": "user-toml", "value": 200000},
                    {"layer": "default", "value": 180000},
                ],
            }
        ],
        "targets": ["user-toml", "project-toml", "overrides"],
    }
}
