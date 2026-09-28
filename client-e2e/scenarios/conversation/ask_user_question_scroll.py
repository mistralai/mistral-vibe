"""Route the wheel by pointer position while a question app is open."""

from __future__ import annotations

from e2e.app_server.events import (
    ask_user_question,
    ask_user_question_added,
    assistant_msg,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1", "VIBE_TYPING_GRACE_PERIOD_MS": "0"}
handshake = {"callback/result": {"accepted": True}}
capture_startup = False
capture_steps = {1, 2, 3}
settle_per_key = True

_WHEEL_OVER_QUESTION = "\x1b[<64;60;34M"
_SHIFT_UP = "\x1b[1;2A"
_REPLY = "\n\n".join(f"Discussion line {index:02d}." for index in range(1, 31))
_QUESTION = [
    {
        "question": "Which database should we use?",
        "options": [{"label": "PostgreSQL"}, {"label": "MongoDB"}],
    }
]

timeline: Timeline = [
    "plan the migration\r",
    turn_started(),
    user_msg("plan the migration"),
    assistant_msg(_REPLY),
    ask_user_question_added(_QUESTION),
    ask_user_question(_QUESTION),
    {"release": 6},
    _WHEEL_OVER_QUESTION,
    _SHIFT_UP,
]
