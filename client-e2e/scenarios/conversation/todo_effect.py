"""A todo effect buckets its items by status with checkbox icons."""

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

_PROMPT = "update the todo list"

# The settled todo disclosure header lands on SGR row 30.
_CLICK = "\x1b[<0;1;30M\x1b[<0;1;30m"

_TODOS = [
    todo_item("1", "Write the parity suite", "completed"),
    todo_item("2", "Run the parity suite", "in_progress"),
    todo_item("3", "File the follow-ups", "pending"),
    todo_item("4", "Port the narrator", "cancelled"),
]

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    todo(_TODOS),
    assistant_msg("Done."),
    turn_completed(),
    _CLICK,
]
