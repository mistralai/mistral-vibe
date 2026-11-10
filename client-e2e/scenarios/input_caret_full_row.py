"""The caret stays visible after the last cell of a full composer row."""

from __future__ import annotations

from e2e.app_server.config import COLUMNS
from e2e.app_server.scenario import Timeline, take_snapshot

env = {"VIBE_THEME": "textual-dark"}
handshake = {
    "config/read": {"config": {"theme": "textual-dark"}},
    "runtime/read": {"runtime": {"config": {"theme": "textual-dark"}}},
}

# The `> ` marker takes 2 columns and the last one is kept for the caret.
_FULL_ROW = "a" * (COLUMNS - 3)

timeline: Timeline = [_FULL_ROW, "b", take_snapshot()]
