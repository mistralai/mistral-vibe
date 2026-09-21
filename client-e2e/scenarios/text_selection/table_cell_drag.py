"""Dragging from a wrapped table cell stays inside that logical cell."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}
clipboard_clients = {"rust"}
expected_clipboard = "ong table cell text wraps instead of disappearing"

_PROMPT = "select within a table cell"
_ANSWER = "\n".join([
    "| Feature | Behavior | Notes |",
    "| --- | --- | --- |",
    (
        "| Table cells | Long table cell text wraps instead of disappearing beyond "
        "the viewport | Neighboring content must stay outside the selection |"
    ),
])
_DRAG = "\x1b[<0;20;30M\x1b[<32;100;30M\x1b[<0;100;30m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
    _DRAG,
]
