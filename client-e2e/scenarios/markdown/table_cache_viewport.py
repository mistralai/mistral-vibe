"""Wrapping tables keep their layout while entering and leaving the viewport."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_delta,
    assistant_msg,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

capture_steps = {1, 2, 3, 5}
env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
settle_per_key = True
screen_contains = {"rust": ("Cache refresh confirmed after streaming update.",)}

_PROMPT = "compare keybindings"
_PRELUDE = "\n\n".join(
    f"Prelude line {index:02d} keeps older content above the table entry."
    for index in range(1, 25)
)
_TABLES = """## Unticketed gaps

| Keybinding | Python behavior | Rust behavior |
|---|---|---|
| PageUp / PageDown | Moves through a long composer by one visible page while preserving the caret and selection | Ignored, leaving the composer on the same visible rows |
| Ctrl+Shift+K | Deletes every line intersecting the selection, including partially selected boundary lines | Deletes only from the caret to the end of its current line |
| Ctrl+X without selection | Cuts the complete current line and keeps adjacent multiline content intact | No-op because no explicit selection exists |
| Ctrl+C with selection | Copies selected text without clearing the draft or moving the caret | Clears the draft before a second press can quit |

## Already tracked

| Keybinding | Ticket | Coverage |
|---|---|---|
| Ctrl+G external editor | [VIBE-4419](https://linear.app/mistral-ai/issue/VIBE-4419) | Opens the configured editor and restores the resulting multiline draft |
| Ctrl+O tool output | VIBE-4423 | Toggles expanded tool output without changing the transcript scroll anchor |
| Ctrl+Backslash debug console | VIBE-4410 | Opens diagnostics while preserving the conversation underneath the panel |
| Ctrl+Z suspend | VIBE-4385 | Restores the terminal before suspension and redraws cleanly after resume |"""
_ASSISTANT_ID = "table-cache-target"
_SHIFT_UP = "\x1b[1;2A"
_SHIFT_DOWN = "\x1b[1;2B"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_PRELUDE, entry_id="table-cache-prelude"),
    assistant_msg(_TABLES, entry_id=_ASSISTANT_ID, generation_status="in_progress"),
    assistant_delta(
        _ASSISTANT_ID, "\n\nCache refresh confirmed after streaming update."
    ),
    turn_completed(),
    {"release": 4},
    _SHIFT_UP * 30,
    _SHIFT_DOWN * 15,
    _SHIFT_DOWN * 15,
    {"release": 2},
]
