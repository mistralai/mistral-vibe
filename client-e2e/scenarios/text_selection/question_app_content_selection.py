"""Question box text is selectable, option clicks fire on release, and the
release auto-copies with the inline notice visible on the loading row.
"""

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
capture_steps = {1, 2, 3}
clipboard_clients = {"rust"}
clipboard_contains = ("Which color scheme do you prefer?",)
screen_contains = {"rust": ("› 2. Nord", "Copied"), "python": ("› 2. Nord", "Copied")}

_QUESTIONS = [
    {
        "question": "Which color scheme do you prefer?",
        "options": [
            {"label": "Solarized", "description": "Warm muted tones"},
            {"label": "Nord", "description": "Cool blue tones"},
        ],
    }
]

# Drag across the title row inside the box (1-based SGR coordinates), then a
# plain click on the second option row: the drag selects, the click navigates.
_DRAG_TITLE = "\x1b[<0;3;32M\x1b[<32;45;32M\x1b[<0;45;32m"
_CLICK_NORD = "\x1b[<0;10;35M\x1b[<0;10;35m"

timeline: Timeline = [
    "pick a color\r",
    turn_started(),
    user_msg("pick a color"),
    ask_user_question_added(_QUESTIONS),
    ask_user_question(_QUESTIONS),
    {"release": 5},
    _DRAG_TITLE,
    _CLICK_NORD,
]

# Each key must land on a settled UI: batching changes the outcome here.
settle_per_key = True
