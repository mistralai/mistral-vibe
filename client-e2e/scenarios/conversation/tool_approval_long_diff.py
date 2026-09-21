"""Keep the tail of over-wide file-edit diffs inspectable."""

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
screen_contains = {"rust": ("-DIFF-TAIL",)}

_INPUT = {
    "filePath": "example.txt",
    "oldString": "delta",
    "newString": "delta-" + "q" * 150 + "-DIFF-TAIL",
}

timeline: Timeline = [
    "edit the long line\r",
    turn_started(),
    user_msg("edit the long line"),
    tool_approval_added("edit", "file_edit", _INPUT),
    tool_approval("edit", "file_edit", _INPUT),
    {"release": 5},
]

settle_per_key = True
