"""Shift+Enter on a locked typed answer neither selects it nor moves on."""

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
    "\x1b[13;2u",
]

screen_contains = ("› 3. SQLite", "e edit")
