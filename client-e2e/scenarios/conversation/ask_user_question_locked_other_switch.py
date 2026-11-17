"""Back on a question answered with typed text, ←→ still switch questions instead of moving the caret."""

from __future__ import annotations

from e2e.app_server.events import (
    ask_user_question,
    ask_user_question_added,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {"VIBE_INPUT_GRACE_PERIOD_MS": "0", "VIBE_TYPING_GRACE_PERIOD_MS": "0"}
handshake = {"callback/result": {"accepted": True}}

_DOWN = "\x1b[B"
_LEFT = "\x1b[D"
_RIGHT = "\x1b[C"

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
    _RIGHT,
]

screen_contains = ("[Framework]", "Which framework?")
