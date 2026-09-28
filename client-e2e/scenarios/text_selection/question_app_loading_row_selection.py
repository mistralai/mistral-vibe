"""The `User input required` loading row is selectable while a question waits."""

from __future__ import annotations

from e2e.app_server.events import (
    ask_user_question,
    ask_user_question_added,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {
    "VIBE_REPLAY_SETTLE_BUSY": "1",
    "VIBE_TYPING_GRACE_PERIOD_MS": "0",
    "SSH_TTY": "/dev/pts/0",
}
handshake = {"callback/result": {"accepted": True}}
capture_startup = False
capture_steps = {1, 2}
clipboard_clients = {"rust"}
clipboard_contains = ("User input required",)

_QUESTIONS = [
    {
        "question": "Which color scheme do you prefer?",
        "options": [{"label": "Solarized"}, {"label": "Nord"}],
    }
]

# Drag along the loading row above the question box (1-based SGR coordinates).
_DRAG_LOADING_ROW = "\x1b[<0;3;30M\x1b[<32;40;30M\x1b[<0;40;30m"

timeline: Timeline = [
    "pick a color\r",
    turn_started(),
    user_msg("pick a color"),
    ask_user_question_added(_QUESTIONS),
    ask_user_question(_QUESTIONS),
    {"release": 5},
    _DRAG_LOADING_ROW,
]

# Each key must land on a settled UI: batching changes the outcome here.
settle_per_key = True
