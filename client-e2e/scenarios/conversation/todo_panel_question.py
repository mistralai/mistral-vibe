"""The plan survives an `ask_user_question` taking the input box, rail item and panel both."""

from __future__ import annotations

from e2e.app_server.events import (
    ask_user_question,
    ask_user_question_added,
    todo,
    todo_item,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {"VIBE_TYPING_GRACE_PERIOD_MS": "0"}
handshake = {"callback/result": {"accepted": True}}

_PROMPT = "plan the migration"
# Terminals without a distinct Cmd key deliver the cmd+\\ remap as ESC \.
_TOGGLE = "\x1b\\"

_TODOS = [
    todo_item("1", "Write the parity suite", "completed"),
    todo_item("2", "Pick the database", "in_progress"),
    todo_item("3", "Port the narrator", "pending"),
]

_QUESTIONS = [
    {
        "question": "Which database should we use for this project?",
        "options": [
            {"label": "PostgreSQL", "description": "Relational database"},
            {"label": "MongoDB", "description": "Document database"},
        ],
    }
]

# One enqueue means one batch, so the plan and the question all hang off the
# prompt; the releases let the panel be docked before the question lands.
timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    todo(_TODOS),
    ask_user_question_added(_QUESTIONS),
    ask_user_question(_QUESTIONS),
    {"release": 4},
    _TOGGLE,
    {"release": 2},
]

# The panel keeps its column and the rail keeps its plan item while the question app owns the input box.
screen_contains = ("Todos · 1/3 done", "Which database should we use", "≡ 1/3")
