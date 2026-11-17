"""A narrow terminal wraps the `ask_user_question` tabs between tabs, answered ones included."""

from __future__ import annotations

from e2e.app_server.events import (
    ask_user_question,
    ask_user_question_added,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline, resize

env = {"VIBE_INPUT_GRACE_PERIOD_MS": "0", "VIBE_TYPING_GRACE_PERIOD_MS": "0"}
handshake = {"callback/result": {"accepted": True}}

_QUESTIONS = [
    {
        "header": header,
        "question": f"Which {header.lower()}?",
        "options": [{"label": "Yes"}, {"label": "No"}],
    }
    for header in (
        "Database",
        "Framework",
        "Authentication",
        "Deployment",
        "Monitoring",
    )
]

timeline: Timeline = [
    resize(40, 50),
    "set up\r",
    turn_started(),
    user_msg("set up"),
    ask_user_question_added(_QUESTIONS),
    ask_user_question(_QUESTIONS),
    {"release": 5},
    "\r",
]

screen_contains = (
    "│  Database ✓   [Framework]   Authentication     │",
    "│  Deployment    Monitoring                      │",
)
