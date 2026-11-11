"""Oversized table cells wrap and reflow after terminal resizes."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline, resize, take_snapshot

screen_contains = (
    "│ Table cells │ Long table cell text wraps",
    "│             │ instead of disappearing",
    "│             │ beyond the viewport",
    "│ borders aligned and │",
    "└─────────────┴──────────────────────────────┴─────────────────────┘",
)

_PROMPT = "compare table wrapping"
_ANSWER = "\n".join([
    "| Feature | Behavior | Notes |",
    "| --- | --- | --- |",
    (
        "| Table cells | Long table cell text wraps instead of disappearing beyond "
        "the viewport | Resizing keeps borders aligned and every word visible |"
    ),
])

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
    take_snapshot(),
    resize(40, 72),
    take_snapshot(),
    resize(40, 96),
    take_snapshot(),
    resize(40, 72),
    take_snapshot(),
]
