"""A soft-wrapped free-text answer copies as one line, keeping its hard newlines."""

from __future__ import annotations

from e2e.app_server.events import (
    ask_user_question,
    ask_user_question_added,
    paste,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {
    "VIBE_REPLAY_SETTLE_BUSY": "1",
    "VIBE_TYPING_GRACE_PERIOD_MS": "0",
    "SSH_TTY": "/dev/pts/0",
}
handshake = {"callback/result": {"accepted": True}}
capture_startup = False
capture_steps = {5, 6}
clipboard_clients = {"rust"}

_LONG = (
    "We need something embedded, ideally SQLite with WAL enabled, because the CLI "
    "runs offline and must never depend on a database server process."
)
clipboard_contains = (f"{_LONG}\nAlso: no daemon",)

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

_DOWN = "\x1b[B"
# From the title row down to the right edge of the field's last row.
_DRAG_TO_FIELD_END = "\x1b[<0;3;29M\x1b[<32;117;36M\x1b[<0;117;36m"

timeline: Timeline = [
    "which db\r",
    turn_started(),
    user_msg("which db"),
    ask_user_question_added(_QUESTIONS),
    ask_user_question(_QUESTIONS),
    {"release": 5},
    _DOWN,
    _DOWN,
    _DOWN,
    paste(f"{_LONG}\nAlso: no daemon"),
    _DRAG_TO_FIELD_END,
]

# Each key must land on a settled UI: batching changes the outcome here.
settle_per_key = True
