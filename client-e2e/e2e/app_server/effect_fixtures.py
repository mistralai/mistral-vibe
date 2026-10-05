"""Replay generic public effect fixtures without invoking backend tool projectors."""

from __future__ import annotations

import json

from e2e.app_server.config import FIXTURE_PATH
from e2e.app_server.events import _added, _entry, _updated
from e2e.app_server.scenario import AppServerEvent


def effect_started(fixture: str, name: str) -> AppServerEvent:
    effect = json.loads((FIXTURE_PATH.parent / fixture).read_text())[name]
    return _added(
        _entry(
            "effect",
            entry_id=name,
            generation_status="in_progress",
            title=effect["title"],
            detail=effect["detail"],
            state={"status": "running", "outputText": ""},
        )
    )


def effect_settled(fixture: str, name: str) -> AppServerEvent:
    effect = json.loads((FIXTURE_PATH.parent / fixture).read_text())[name]
    return _updated(
        name,
        [
            {"op": "replace", "path": "/state", "value": effect["state"]},
            {"op": "replace", "path": "/generationStatus", "value": "completed"},
        ],
    )
