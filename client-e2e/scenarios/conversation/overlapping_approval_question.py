"""Answering an approval leaves the overlapping question waiting for input."""

from __future__ import annotations

from e2e.app_server.events import (
    ask_user_question,
    ask_user_question_added,
    tool_approval,
    tool_approval_added,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {
    "VIBE_REPLAY_SETTLE_BUSY": "1",
    "VIBE_TYPING_GRACE_PERIOD_MS": "0",
    "VIBE_INPUT_GRACE_PERIOD_MS": "0",
}
handshake = {"callback/result": {"accepted": True}}
capture_startup = False
capture_steps = {1, 2}
expected_titles = ("? Vibe", "? Vibe")
_QUESTIONS = [{"question": "Which mode?", "options": [{"label": "Fast"}]}]
_INPUT = {"command": "printf 'approved\\n'"}

timeline: Timeline = [
    "run and ask\r",
    turn_started(),
    user_msg("run and ask"),
    ask_user_question_added(_QUESTIONS),
    ask_user_question(_QUESTIONS, request_id=9001),
    tool_approval_added("bash", "shell", _INPUT),
    tool_approval("bash", "shell", _INPUT, request_id=9002),
    {"release": 7},
    "\r",
]

settle_per_key = True
screen_contains = ("Which mode?",)
