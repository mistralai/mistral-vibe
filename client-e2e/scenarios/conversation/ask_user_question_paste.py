"""Pasting into the question app's free-text row, never the composer."""

from __future__ import annotations

from e2e.app_server.events import (
    ask_user_question,
    ask_user_question_added,
    paste,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

# The turn stays open: the tool blocks on the callback until the user answers,
# so every frame settles while the client is still busy. The typing grace period
# is zeroed so the app takes the input box in the frame it is delivered in.
env = {"VIBE_REPLAY_SETTLE_BUSY": "1", "VIBE_TYPING_GRACE_PERIOD_MS": "0"}
handshake = {"callback/result": {"accepted": True}}
# The prompt frame races the handshake under `SETTLE_BUSY`; capture from the
# released callback onward.
capture_startup = False
capture_steps = {1, 2, 3, 4, 5}
screen_contains = {"rust": ("SQLite Enterprise",), "python": ("SQLite Enterprise",)}
screen_excludes = {"rust": ("Ignored second line",), "python": ("Ignored second line",)}

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
    # Only the first pasted line may land, and only in the free-text row.
    paste("SQLite Enterprise\nIgnored second line"),
]

# Each key must land on a settled UI: batching changes the outcome here.
settle_per_key = True
