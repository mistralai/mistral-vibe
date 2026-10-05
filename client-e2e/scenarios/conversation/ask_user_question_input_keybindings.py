"""Composer editing keybindings on the question app's free-text row."""

from __future__ import annotations

from e2e.app_server.events import (
    ask_user_question,
    ask_user_question_added,
    ask_user_question_answered,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

# The turn stays open while the callback blocks, and the typing grace period
# is zeroed so the app takes typed keys in the frame they are delivered in.
env = {"VIBE_REPLAY_SETTLE_BUSY": "1", "VIBE_TYPING_GRACE_PERIOD_MS": "0"}
handshake = {"callback/result": {"accepted": True}}
capture_startup = False
# Frames that show the caret moves and the two submitted answers.
capture_steps = {1, 4, 5, 7, 9, 10, 12, 14, 15, 16}
request_methods = {"callback/result"}

_Q1 = "Which database should we use for this project?"
_Q2 = "Where should the cache live?"
_QUESTIONS = [
    {
        "question": _Q1,
        "options": [
            {"label": "PostgreSQL", "description": "Relational database"},
            {"label": "MongoDB", "description": "Document database"},
            {"label": "Redis", "description": "In-memory store"},
        ],
    },
    {
        "header": "Cache",
        "question": _Q2,
        "options": [
            {"label": "In-process", "description": "Per-node memory"},
            {"label": "Sidecar", "description": "Local redis container"},
        ],
    },
]

on_request = {
    "callback/result": [
        ask_user_question_answered([
            {"question": _Q1, "answer": "X hello world!", "isOther": True},
            {"question": _Q2, "answer": "final", "isOther": True},
        ]),
        turn_completed(),
    ]
}

_DOWN = "\x1b[B"
# Ctrl+A, Ctrl+E and Ctrl+U as they arrive from a terminal.
_CTRL_A = "\x01"
_CTRL_E = "\x05"
_CTRL_U = "\x15"

timeline: Timeline = [
    "which db\r",
    turn_started(),
    user_msg("which db"),
    ask_user_question_added(_QUESTIONS),
    ask_user_question(_QUESTIONS),
    {"release": 5},
    # Park the cursor on the first question's free-text row.
    _DOWN,
    _DOWN,
    _DOWN,
    "hello world",
    # Ctrl+A then typing inserts at the start; Ctrl+E then typing appends.
    _CTRL_A,
    "X ",
    _CTRL_E,
    "!",
    "\r",
    # The app moves to the second question: Ctrl+U clears the typed draft.
    _DOWN,
    _DOWN,
    "keep everything",
    _CTRL_U,
    "final",
    "\r",
]

# Each key must land on a settled UI: batching changes the outcome here.
settle_per_key = True
