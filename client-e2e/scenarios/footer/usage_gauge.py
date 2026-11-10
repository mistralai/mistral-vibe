"""The account's monthly Vibe Code usage paints as a gauge between the model and the context gauge."""

from __future__ import annotations

from e2e.app_server.config import COLUMNS, ROWS
from e2e.app_server.scenario import Timeline, take_snapshot

handshake = {
    "account/read": {
        "account": {
            "status": "ready",
            "plan": {"kind": "chat", "name": "TEAM", "title": "Team"},
            "planOffer": None,
            "rateLimitAction": None,
            "budget": {"usagePercentage": 40.0, "paygEnabled": False},
        }
    }
}

_LEFT = "/test/workdir"
_RIGHT = "usage ██▍    40% · ctx        0/600k"
screen_rows = {ROWS - 1: _LEFT.ljust(COLUMNS - len(_RIGHT)) + _RIGHT}

timeline: Timeline = [take_snapshot()]
