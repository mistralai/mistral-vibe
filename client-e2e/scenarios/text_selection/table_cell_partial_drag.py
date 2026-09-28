"""Selecting part of one table-cell row ignores later wrapped rows safely."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}
clipboard_clients = {"rust"}
expected_clipboard = "ong ta"

_PROMPT = "select part of a table cell"
_ANSWER = "\n".join([
    "| Feature | Behavior | Notes |",
    "| --- | --- | --- |",
    (
        "| Table cells | Long table cell text wraps instead of disappearing beyond "
        "the viewport while additional words keep this same logical cell flowing "
        "across several rendered rows for selection coverage | Neighboring content |"
    ),
])
_DRAG = "\x1b[<0;18;29M\x1b[<32;23;29M\x1b[<0;23;29m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
    _DRAG,
]
