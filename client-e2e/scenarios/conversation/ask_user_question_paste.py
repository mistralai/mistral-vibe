"""Pasting into the question app's free-text row keeps every line, never the composer."""

from __future__ import annotations

from e2e.app_server.events import (
    ask_user_question,
    ask_user_question_added,
    paste,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

# The typing grace period is zeroed so the app takes the input box in the frame
# it is delivered in.
env = {"VIBE_TYPING_GRACE_PERIOD_MS": "0"}
handshake = {"callback/result": {"accepted": True}}
screen_contains = ("SQLite Enterprise", "with WAL")

_DOWN = "\x1b[B"

_QUESTIONS = [
    {
        "question": "Which database should we use for this project?",
        "options": [
            {"label": "PostgreSQL", "description": "Relational database"},
            {"label": "MongoDB", "description": "Document database"},
            {"label": "Redis", "description": "In-memory store"},
        ],
    }
]

timeline: Timeline = [
    "which db\r",
    turn_started(),
    user_msg("which db"),
    ask_user_question_added(_QUESTIONS),
    ask_user_question(_QUESTIONS),
    {"release": 5},
    # Three downs park the cursor on the free-text row, which then takes keys.
    _DOWN,
    _DOWN,
    _DOWN,
    # Every pasted line lands, and only in the free-text row.
    paste("SQLite Enterprise\nwith WAL"),
]
