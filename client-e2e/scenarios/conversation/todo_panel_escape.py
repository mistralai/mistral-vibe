"""Esc closes the docked plan panel before it interrupts a running turn."""

from __future__ import annotations

from e2e.app_server.events import todo, todo_item, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1", "VIBE_TYPING_GRACE_PERIOD_MS": "0"}
capture_startup = False

_PROMPT = "plan the migration"
# Terminals without a distinct Cmd key deliver the cmd+\\ remap as ESC \.
_TOGGLE = "\x1b\\"

_TODOS = [
    todo_item("1", "Write the parity suite", "completed"),
    todo_item("2", "Pick the database", "in_progress"),
    todo_item("3", "Port the narrator", "pending"),
]

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    todo(_TODOS),
    _TOGGLE,
    "\x1b",
]

# The first Esc only undocks the panel: the turn keeps running.
screen_contains = {"rust": ("Esc/Ctrl+C to interrupt",)}
screen_excludes = {"rust": ("Todos · 1/3 done", "Interrupted")}
