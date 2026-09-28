"""Wrap and mouse-scroll long `ask_user_question` content."""

from __future__ import annotations

from e2e.app_server.events import (
    ask_user_question,
    ask_user_question_added,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1", "VIBE_TYPING_GRACE_PERIOD_MS": "0"}
handshake = {"callback/result": {"accepted": True}}
capture_startup = False
capture_steps = {1, 2, 3}
screen_excludes = {"rust": ("› 1. Wrap the content",)}

_WHEEL_DOWN = "\x1b[<65;10;13M"
_CLICK_PARTIAL_OPTION = "\x1b[<0;10;13M\x1b[<0;10;13m"
_QUESTIONS = [
    {
        "question": "Which implementation should we choose when the explanation is long enough that it must wrap cleanly inside the question application's right border?",
        "options": [
            {
                "label": "Wrap the content",
                "description": "Keep every part of the option visible by continuing it on the next terminal row instead of clipping it at the edge.",
            },
            *({"label": f"Alternative {index}"} for index in range(2, 25)),
        ],
        "hideOther": True,
    }
]

timeline: Timeline = [
    "show a long question\r",
    turn_started(),
    user_msg("show a long question"),
    ask_user_question_added(_QUESTIONS),
    ask_user_question(_QUESTIONS),
    {"release": 5},
    _WHEEL_DOWN * 2,
    _CLICK_PARTIAL_OPTION,
]

settle_per_key = True
