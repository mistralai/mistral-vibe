"""Resuming a session without todos drops the previous session's pinned todo row."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    todo,
    todo_item,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_UP = "\x1b[A"

timeline: Timeline = [
    "plan it\r",
    turn_started(),
    user_msg("plan it"),
    todo([
        todo_item("1", "Write the parity suite", "completed"),
        todo_item("2", "File the follow-ups", "in_progress"),
    ]),
    assistant_msg("Planned."),
    turn_completed(),
    # Up highlights the saved session, which never wrote todos; Enter resumes it.
    "/resume",
    "\r",
    _UP,
    "\r",
]

capture_startup = False
capture_steps = {0, 4}
screen_contains = ("Saved session restored.",)
screen_excludes = ("File the follow-ups",)
