"""Ctrl+C cancels an open question before it interrupts the turn, as Esc does."""

from __future__ import annotations

from e2e.app_server.events import (
    ask_user_question,
    ask_user_question_added,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {
    "VIBE_REPLAY_SETTLE_BUSY": "1",
    "VIBE_TYPING_GRACE_PERIOD_MS": "0",
    "VIBE_INPUT_GRACE_PERIOD_MS": "0",
}
handshake = {"callback/result": {"accepted": True}}
capture_startup = False

_PROMPT = "set up the project"

_QUESTIONS = [
    {
        "question": "Which database should we use for this project?",
        "options": [
            {"label": "PostgreSQL", "description": "Relational database"},
            {"label": "MongoDB", "description": "Document database"},
        ],
    }
]

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    ask_user_question_added(_QUESTIONS),
    ask_user_question(_QUESTIONS),
    {"release": 5},
    "\x03",
]

# The question closes as cancelled; the turn keeps running.
screen_contains = {"rust": ("Esc/Ctrl+C to interrupt",)}
screen_excludes = {"rust": ("↑↓/jk navigate", "Interrupted")}
