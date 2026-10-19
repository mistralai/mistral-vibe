"""Tab across a multi-question `ask_user_question` and tick a multi-select box."""

from __future__ import annotations

from e2e.app_server.events import (
    ask_user_question,
    ask_user_question_added,
    ask_user_question_answered,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {
    "VIBE_INPUT_GRACE_PERIOD_MS": "0",
    "VIBE_REPLAY_SETTLE_BUSY": "1",
    "VIBE_TYPING_GRACE_PERIOD_MS": "0",
}
handshake = {"callback/result": {"accepted": True}}
# The prompt frame races the handshake under `SETTLE_BUSY`; capture from the
# released callback onward.
capture_startup = False
capture_steps = {1, 2, 3, 4}

_RIGHT = "\x1b[C"
_LEFT = "\x1b[D"
# Press and release on the "Caching" row, in 1-based terminal coordinates; one
# click ticks it like Enter, without moving the highlight there first.
_CLICK_CACHING = "\x1b[<0;10;34M\x1b[<0;10;34m"

timeline: Timeline = [
    "set up the stack\r",
    turn_started(),
    user_msg("set up the stack"),
    # The projector broadcasts the callback history entry and later marks it
    # answered; neither may render a transcript row.
    ask_user_question_added([
        {
            "header": "DB",
            "question": "Which database?",
            "options": [{"label": "PostgreSQL"}, {"label": "MongoDB"}],
        },
        {
            "header": "Features",
            "question": "Which features do you want to enable?",
            "options": [
                {"label": "Authentication", "description": "User login/logout"},
                {"label": "Caching", "description": "Redis caching layer"},
            ],
            "multiSelect": True,
            "hideOther": True,
        },
    ]),
    ask_user_question([
        {
            "header": "DB",
            "question": "Which database?",
            "options": [{"label": "PostgreSQL"}, {"label": "MongoDB"}],
        },
        {
            "header": "Features",
            "question": "Which features do you want to enable?",
            "options": [
                {"label": "Authentication", "description": "User login/logout"},
                {"label": "Caching", "description": "Redis caching layer"},
            ],
            "multiSelect": True,
            "hideOther": True,
        },
    ]),
    ask_user_question_answered([
        {"question": "Which database?", "answer": "PostgreSQL", "isOther": False}
    ]),
    {"release": 6},
    _RIGHT,
    _CLICK_CACHING,
    _LEFT,
]

# Each key must land on a settled UI: batching changes the outcome here.
settle_per_key = True
