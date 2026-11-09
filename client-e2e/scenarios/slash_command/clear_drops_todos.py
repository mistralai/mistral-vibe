"""`/clear` empties the todo list, so the pinned todo row disappears with the conversation."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    cleared_state,
    todo,
    todo_item,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

handshake = {"session/history/clear": {"state": cleared_state()}}

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
    "/clear\r",
]

capture_startup = False
screen_contains = ("New conversation started.",)
screen_excludes = ("File the follow-ups",)
