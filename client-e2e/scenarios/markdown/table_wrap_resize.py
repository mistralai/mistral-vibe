"""Oversized table cells wrap and reflow after terminal resizes."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline, resize

screen_contains = {
    "rust": (
        "│ Table   │ Long table cell text wraps",
        "│ cells   │ instead of disappearing",
        "│         │ beyond the viewport",
        "│ aligned and every word",
        "└─────────┴───────────────────────────────┴────────────────────────┘",
    )
}

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
    resize(40, 72),
    resize(40, 96),
    resize(40, 72),
]
