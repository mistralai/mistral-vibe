"""File-edit approval diffs wrap at word boundaries and hang under the gutter."""

from __future__ import annotations

from e2e.app_server.events import (
    tool_approval,
    tool_approval_added,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline, take_snapshot

env = {"VIBE_TYPING_GRACE_PERIOD_MS": "0"}

_INPUT = {
    "filePath": "notes.md",
    "oldString": "When text wraps in the diff view, the text is split at the end of the line.",
    "newString": (
        "When text wraps in the approval diff, the line break respects word separators and "
        "only breaks inside a word when that word is longer than the line: "
        + "unbreakable" * 12
        + " end."
    ),
}

timeline: Timeline = [
    "reword the paragraph\r",
    turn_started(),
    user_msg("reword the paragraph"),
    tool_approval_added("edit", "file_edit", _INPUT),
    tool_approval("edit", "file_edit", _INPUT),
    {"release": 5},
    take_snapshot(),
]
