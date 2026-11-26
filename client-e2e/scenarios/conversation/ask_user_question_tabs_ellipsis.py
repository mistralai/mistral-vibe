"""A tab wider than the box ellipsizes its header and keeps its brackets and `✓`."""

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

_LEFT = "\x1b[D"

_QUESTIONS = [
    {
        "header": header,
        "question": "Which one?",
        "options": [{"label": "Yes"}, {"label": "No"}],
    }
    for header in ("Authentication", "DB")
]

timeline: Timeline = [
    resize(40, 18),
    "set up\r",
    turn_started(),
    user_msg("set up"),
    ask_user_question_added(_QUESTIONS),
    ask_user_question(_QUESTIONS),
    {"release": 5},
    "\r",
    _LEFT,
]

screen_contains = ("│ [Authentic… ✓] │", "│  DB            │")
