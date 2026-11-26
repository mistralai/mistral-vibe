"""Shared config editor scenario fixtures."""

from __future__ import annotations

from typing import Any

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

TARGETS = ["user-toml", "project-toml", "overrides"]


def field(
    name: str,
    kind: str,
    value: Any,
    *,
    layers: list[tuple[str, Any]] | None = None,
    choices: list[str] | None = None,
) -> dict[str, Any]:
    """One popular `/config` row; `layers` lists its layer values, highest first."""
    return {
        "name": name,
        "kind": kind,
        "value": value,
        "description": "",
        "path": f"/{name}",
        "popular": True,
        "enumChoices": choices or [],
        "valueLabels": {},
        "layerValues": [
            {"layer": layer, "value": v} for layer, v in layers or [("default", value)]
        ],
    }


def fields_read(*fields: dict[str, Any]) -> dict[str, Any]:
    """A `config/fields/read` answer listing only `fields`."""
    return {"fields": list(fields), "targets": TARGETS}
