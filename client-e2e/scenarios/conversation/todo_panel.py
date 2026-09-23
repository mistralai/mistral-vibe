"""A settled todo write pins a plan line above the input; cmd+\\ docks it right."""

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
# Terminals without a distinct Cmd key deliver the cmd+\\ remap as ESC \.
_TOGGLE = "\x1b\\"

_TODOS = [
    todo_item("1", "Write the parity suite", "completed"),
    todo_item("2", "Run the parity suite", "completed"),
    todo_item("3", "File the follow-ups", "in_progress"),
    todo_item("4", "Port the narrator", "cancelled"),
]

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    todo(_TODOS),
    assistant_msg("Done."),
    turn_completed(),
    _TOGGLE,
    _TOGGLE,
]
