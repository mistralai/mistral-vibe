"""A question-box drag copies nothing and shows no notice when autocopy is off."""

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
handshake = {
    "callback/result": {"accepted": True},
    "config/read": {"config": {"autocopyToClipboard": False}},
    "runtime/read": {"runtime": {"config": {"autocopyToClipboard": False}}},
}
capture_startup = False
capture_steps = {1, 2}
screen_contains = {"rust": ("Which color scheme do you prefer?",)}
screen_excludes = {"rust": ("Copied",), "python": ("Copied",)}

_QUESTIONS = [
    {
        "question": "Which color scheme do you prefer?",
        "options": [
            {"label": "Solarized", "description": "Warm muted tones"},
            {"label": "Nord", "description": "Cool blue tones"},
        ],
    }
]

# Drag across the title row inside the box (1-based SGR coordinates): the
# selection paints, but the release neither copies nor shows the notice.
_DRAG_TITLE = "\x1b[<0;3;32M\x1b[<32;45;32M\x1b[<0;45;32m"

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
