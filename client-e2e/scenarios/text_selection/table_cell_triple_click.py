"""Triple-clicking a wrapped table cell copies its logical unwrapped text."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}
clipboard_clients = {"rust"}
expected_clipboard = (
    "Long table cell text wraps instead of disappearing beyond the viewport"
)

_PROMPT = "select a table cell"
_ANSWER = "\n".join([
    "| Feature | Behavior | Notes |",
    "| --- | --- | --- |",
    (
        "| Table cells | Long table cell text wraps instead of disappearing beyond "
        "the viewport | Resizing keeps borders aligned and every word visible |"
    ),
])
_CLICK = "\x1b[<0;20;30M\x1b[<0;20;30m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
    _CLICK * 3,
]
