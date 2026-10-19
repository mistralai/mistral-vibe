"""The focused free-text row is a full bar, and a click on its prefix unticks a typed answer."""

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

env = {
    "VIBE_REPLAY_SETTLE_BUSY": "1",
    "VIBE_TYPING_GRACE_PERIOD_MS": "0",
    "VIBE_INPUT_GRACE_PERIOD_MS": "0",
}
handshake = {"callback/result": {"accepted": True}}
capture_startup = False
capture_steps = {4, 5, 6}
request_methods = {"callback/result"}

_QUESTION = "Which features do you want to enable?"
_QUESTIONS = [
    {
        "header": "Features",
        "question": _QUESTION,
        "options": [
            {"label": "Authentication", "description": "User login/logout"},
            {"label": "Caching", "description": "Redis caching layer"},
        ],
        "multiSelect": True,
    }
]

on_request = {
    "callback/result": [
        ask_user_question_answered([
            {"question": _QUESTION, "answer": "Authentication", "isOther": False}
        ]),
        turn_completed(),
    ]
}

_DOWN = "\x1b[B"
_SPACE = " "
# Press and release on the `3.` of the focused free-text row's prefix, in
# 1-based terminal coordinates.
_CLICK_PREFIX = "\x1b[<0;5;34M\x1b[<0;5;34m"

timeline: Timeline = [
    "set up the stack\r",
    turn_started(),
    user_msg("set up the stack"),
    ask_user_question_added(_QUESTIONS),
    ask_user_question(_QUESTIONS),
    {"release": 5},
    _SPACE,
    _DOWN,
    _DOWN,
    # Typing ticks the free-text row.
    "redis",
    # A click on its prefix unticks it and keeps the typed answer.
    _CLICK_PREFIX,
    _DOWN,
    # Only the ticked option is submitted.
    "\r",
]

# Each key must land on a settled UI: batching changes the outcome here.
settle_per_key = True
