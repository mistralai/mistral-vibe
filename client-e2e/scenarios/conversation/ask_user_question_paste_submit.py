"""Pasted free text is the answer sent back on the `callback/result`."""

from __future__ import annotations

from e2e.app_server.events import (
    ask_user_question,
    ask_user_question_added,
    ask_user_question_answered,
    paste,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1", "VIBE_TYPING_GRACE_PERIOD_MS": "0"}
handshake = {"callback/result": {"accepted": True}}
capture_startup = False
capture_steps = {1, 2, 3, 4, 5, 6, 7}
request_methods = {"callback/result"}
screen_excludes = {
    "rust": ("Ignored second line", "Junk answer"),
    "python": ("Ignored second line", "Junk answer"),
}

_QUESTION = "Which database should we use for this project?"
_QUESTIONS = [
    {
        "question": _QUESTION,
        "options": [
            {"label": "PostgreSQL", "description": "Relational database"},
            {"label": "MongoDB", "description": "Document database"},
            {"label": "Redis", "description": "In-memory store"},
        ],
    }
]

on_request = {
    "callback/result": [
        ask_user_question_answered([
            {"question": _QUESTION, "answer": "SQLite Enterprise", "isOther": True}
        ]),
        turn_completed(),
    ]
}

_DOWN = "\x1b[B"

timeline: Timeline = [
    "which db\r",
    turn_started(),
    user_msg("which db"),
    ask_user_question_added(_QUESTIONS),
    ask_user_question(_QUESTIONS),
    {"release": 5},
    # The cursor still sits on the first option row: the paste must be dropped.
    paste("Junk answer\nIgnored second line"),
    # Three downs park the cursor on the free-text row, which then takes keys.
    _DOWN,
    _DOWN,
    _DOWN,
    paste("SQLite Enterprise\nIgnored second line"),
    "\r",
]

# Each key must land on a settled UI: batching changes the outcome here.
settle_per_key = True
