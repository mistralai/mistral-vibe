"""Render embedded Markdown fences as literal approval content."""

from __future__ import annotations

from e2e.app_server.events import (
    tool_approval,
    tool_approval_added,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1", "VIBE_TYPING_GRACE_PERIOD_MS": "0"}
capture_startup = False
capture_steps = {1}
screen_contains = {
    "python": ("before fence", "```", "-CODE-TAIL", "after fence"),
    "rust": ("before fence", "```", "-CODE-TAIL", "after fence"),
}

_INPUT = {
    "filePath": "notes.txt",
    "content": "before fence\n```\nwide-" + "z" * 150 + "-CODE-TAIL\nafter fence",
}

timeline: Timeline = [
    "write the file\r",
    turn_started(),
    user_msg("write the file"),
    tool_approval_added("write_file", "file_write", _INPUT),
    tool_approval("write_file", "file_write", _INPUT),
    {"release": 5},
]

settle_per_key = True
