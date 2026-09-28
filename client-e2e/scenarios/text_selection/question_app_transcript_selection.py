"""Selecting transcript text while the question app is open copies it."""

from __future__ import annotations

from e2e.app_server.events import (
    ask_user_question,
    ask_user_question_added,
    assistant_msg,
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
clipboard_contains = ("Bright violet machines",)

_SENTENCE = "Bright violet machines hum quietly in the northern hall."
_QUESTIONS = [
    {
        "question": "Which color scheme do you prefer?",
        "options": [
            {"label": "Solarized", "description": "Warm muted tones"},
            {"label": "Nord", "description": "Cool blue tones"},
        ],
    }
]

_DRAG = "\x1b[<0;3;28M\x1b[<32;119;28M\x1b[<0;119;28m"

timeline: Timeline = [
    "pick a color\r",
    turn_started(),
    user_msg("pick a color"),
    assistant_msg(_SENTENCE),
    ask_user_question_added(_QUESTIONS),
    ask_user_question(_QUESTIONS),
    {"release": 6},
    _DRAG,
]

# Each key must land on a settled UI: batching changes the outcome here.
settle_per_key = True
