"""Space ticks and unticks multi-select checkboxes, never submitting the question."""

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
capture_steps = {1, 2, 5, 7, 8}
request_methods = {"callback/result"}

_QUESTION = "Which features do you want to enable?"
_QUESTIONS = [
    {
        "header": "Features",
        "question": _QUESTION,
        "options": [
            {"label": "Authentication", "description": "User login/logout"},
            {"label": "Caching", "description": "Redis caching layer"},
            {"label": "Logging", "description": "Structured logging"},
        ],
        "multiSelect": True,
        "hideOther": True,
    }
]

on_request = {
    "callback/result": [
        ask_user_question_answered([
            {
                "question": _QUESTION,
                "answer": "Authentication, Logging",
                "isOther": False,
            }
        ]),
        turn_completed(),
    ]
}

_DOWN = "\x1b[B"
_SPACE = " "

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
    _SPACE,
    _DOWN,
    # Space on the submit row is a no-op: only Enter submits.
    _SPACE,
    "\r",
]

# Each key must land on a settled UI: batching changes the outcome here.
settle_per_key = True
