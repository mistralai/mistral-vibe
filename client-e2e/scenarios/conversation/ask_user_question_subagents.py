"""Two subagents ask at once: both questions show in turn and each answer reaches its asker."""

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
    "VIBE_INPUT_GRACE_PERIOD_MS": "0",
}
handshake = {"callback/result": {"accepted": True}}
capture_startup = False
capture_steps = {1, 2, 3}

_CHILD_A = "00000000-0000-4000-8000-00000000000a"
_CHILD_B = "00000000-0000-4000-8000-00000000000b"


def _questions(repo: str) -> list[dict[str, object]]:
    return [
        {
            "question": f"May I touch repo {repo}?",
            "options": [{"label": "Yes"}, {"label": "No"}],
            "hideOther": True,
        }
    ]


timeline: Timeline = [
    "delegate both\r",
    turn_started(),
    user_msg("delegate both"),
    ask_user_question_added(
        _questions("A"), callback_id="callback-a", session_id=_CHILD_A
    ),
    ask_user_question(
        _questions("A"), callback_id="callback-a", request_id=9001, session_id=_CHILD_A
    ),
    ask_user_question_added(
        _questions("B"), callback_id="callback-b", session_id=_CHILD_B
    ),
    ask_user_question(
        _questions("B"), callback_id="callback-b", request_id=9002, session_id=_CHILD_B
    ),
    {"release": 7},
    "\r",
    "\r",
]

settle_per_key = True
screen_excludes = ("May I touch repo",)
