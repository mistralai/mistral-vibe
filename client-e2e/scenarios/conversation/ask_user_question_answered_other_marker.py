"""Back on a question answered with typed text, `›` marks the free-text row and its copy skips the prefix."""

from __future__ import annotations

from e2e.app_server.events import (
    ask_user_question,
    ask_user_question_added,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {
    "VIBE_INPUT_GRACE_PERIOD_MS": "0",
    "VIBE_TYPING_GRACE_PERIOD_MS": "0",
    "SSH_TTY": "/dev/pts/0",
}
handshake = {"callback/result": {"accepted": True}}

_DOWN = "\x1b[B"
_LEFT = "\x1b[D"
# Focus PostgreSQL, then drag from MongoDB to the end of the typed answer (1-based SGR).
_DRAG_ROW = "\x1b[<0;8;35M\x1b[<32;14;36M\x1b[<0;14;36m"

_QUESTIONS = [
    {
        "header": "DB",
        "question": "Which database?",
        "options": [{"label": "PostgreSQL"}, {"label": "MongoDB"}],
    },
    {
        "header": "Framework",
        "question": "Which framework?",
        "options": [{"label": "Django"}, {"label": "FastAPI"}],
    },
]

timeline: Timeline = [
    "set up\r",
    turn_started(),
    user_msg("set up"),
    ask_user_question_added(_QUESTIONS),
    ask_user_question(_QUESTIONS),
    {"release": 5},
    _DOWN,
    _DOWN,
    "SQLite",
    "\r",
    _LEFT,
    _DOWN,
    _DRAG_ROW,
]

screen_contains = ("› 3. SQLite",)
expected_clipboard = "MongoDB\nSQLite"
