"""Question box option prefixes are chrome: never selected, still clickable."""

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
# The drag starts on the question title and ends on the first option's label,
# so the copy crosses the prefix cells without ever including them.
clipboard_contains = (
    "Which color scheme do you prefer?",
    "Solarized - Warm muted tones",
)
clipboard_excludes = ("1.", "›")
screen_contains = {"rust": ("› 1. Solarized",)}

_QUESTIONS = [
    {
        "question": "Which color scheme do you prefer?",
        "options": [
            {"label": "Solarized", "description": "Warm muted tones"},
            {"label": "Nord", "description": "Cool blue tones"},
        ],
    }
]

# A press on the "› 1." prefix anchors no selection, so dragging along the
# option row copies nothing and never navigates (1-based SGR coordinates).
_PREFIX_DRAG = "\x1b[<0;4;34M\x1b[<32;30;34M\x1b[<0;30;34m"
# Drag from the title row down across the first option's label: the copy keeps
# the title and the label but cuts the prefix cells out of the option row.
_DRAG_ACROSS_OPTION = "\x1b[<0;3;32M\x1b[<32;35;34M\x1b[<0;35;34m"

timeline: Timeline = [
    "pick a color\r",
    turn_started(),
    user_msg("pick a color"),
    ask_user_question_added(_QUESTIONS),
    ask_user_question(_QUESTIONS),
    {"release": 5},
    _PREFIX_DRAG,
    _DRAG_ACROSS_OPTION,
]

# Each key must land on a settled UI: batching changes the outcome here.
settle_per_key = True
