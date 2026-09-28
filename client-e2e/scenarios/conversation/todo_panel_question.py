"""The plan survives an `ask_user_question` taking the input box, row and panel both."""

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

# The tool blocks on the callback, so every frame settles while still busy.
env = {"VIBE_REPLAY_SETTLE_BUSY": "1", "VIBE_TYPING_GRACE_PERIOD_MS": "0"}
handshake = {"callback/result": {"accepted": True}}
capture_startup = False

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

# The panel keeps its column and the pinned row keeps its line, while the question
# app owns the input box: `▶ 1/3 · Pick the database` is the row's summary.
screen_contains = {
    "rust": (
        "Todos · 1/3 done",
        "Which database should we use",
        "1/3 · Pick the database",
    )
}
