"""A wrapped question-box title copies as the one line the question holds."""

from __future__ import annotations

from e2e.app_server.events import (
    ask_user_question,
    ask_user_question_added,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

# The turn stays open: the tool blocks on the callback until the user answers.
env = {
    "VIBE_REPLAY_SETTLE_BUSY": "1",
    "VIBE_TYPING_GRACE_PERIOD_MS": "0",
    "SSH_TTY": "/dev/pts/0",
}
handshake = {"callback/result": {"accepted": True}}
capture_startup = False
clipboard_clients = {"rust"}

_QUESTION = (
    "Which color scheme should the terminal use when the question is long enough to wrap"
    " inside the box, so its copy must rejoin the rows?"
)
expected_clipboard = _QUESTION

_QUESTIONS = [
    {
        "question": _QUESTION,
        "options": [
            {"label": "Solarized", "description": "Warm muted tones"},
            {"label": "Nord", "description": "Cool blue tones"},
        ],
    }
]

# Drag across both title rows inside the box (1-based SGR coordinates).
_DRAG_TITLE = "\x1b[<0;3;31M\x1b[<32;118;32M\x1b[<0;118;32m"

timeline: Timeline = [
    "pick a color\r",
    turn_started(),
    user_msg("pick a color"),
    ask_user_question_added(_QUESTIONS),
    ask_user_question(_QUESTIONS),
    {"release": 5},
    _DRAG_TITLE,
]

# Each key must land on a settled UI: batching changes the outcome here.
settle_per_key = True
