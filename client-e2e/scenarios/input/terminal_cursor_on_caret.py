"""Typing while the loader animates parks the terminal cursor on the caret, not in the loader."""

from __future__ import annotations

from e2e.app_server.events import turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
capture_startup = False

_PROMPT = "write the migration"

timeline: Timeline = [f"{_PROMPT}\r", turn_started(), user_msg(_PROMPT), "déjà"]

# Dead-key and IME previews draw at the terminal cursor: it must sit on the caret cell.
screen_cursor = {"rust": (35, 6)}
