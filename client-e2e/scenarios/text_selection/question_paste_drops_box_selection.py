"""Pasting into the question's free-text row drops the box selection above it."""

from __future__ import annotations

from e2e.app_server.events import (
    ask_user_question,
    ask_user_question_added,
    paste,
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
capture_steps = {3, 4}
clipboard_clients = {"rust"}
expected_clipboard = "Which color scheme do you prefer?"
screen_contains = {"rust": ("violet",)}

_QUESTIONS = [
    {
        "question": "Which color scheme do you prefer?",
        "options": [
            {"label": "Solarized", "description": "Warm muted tones"},
            {"label": "Nord", "description": "Cool blue tones"},
        ],
    }
]

_DOWN = "\x1b[B"
# Drag across the question title inside the box (1-based SGR coordinates).
_DRAG_TITLE = "\x1b[<0;3;32M\x1b[<32;45;32M\x1b[<0;45;32m"

timeline: Timeline = [
    "pick a color\r",
    turn_started(),
    user_msg("pick a color"),
    ask_user_question_added(_QUESTIONS),
    ask_user_question(_QUESTIONS),
    {"release": 5},
    _DOWN + _DOWN,
    _DRAG_TITLE,
    paste("violet"),
]

# Each key must land on a settled UI: batching changes the outcome here.
settle_per_key = True
