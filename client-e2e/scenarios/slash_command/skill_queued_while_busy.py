"""A skill queued behind a turn with a merged follow-up loads once promoted; a busy shell command is refused."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    loaded_skill,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_NAME = "code-review"
_SKILL_PROMPT = f"/{_NAME} please"
_FOLLOW_UP = "follow-up prompt"
_BODY = f'<skill_content name="{_NAME}">\nReview twice.\n</skill_content>'
_SKILLS = [
    {
        "name": _NAME,
        "description": "Review the current change.",
        "prompt": "",
        "userInvocable": True,
        "source": "local",
    }
]

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
handshake = {"runtime/read": {"runtime": {"skills": _SKILLS}}}
settle_per_key = True
capture_steps = {3, 5, 7}
screen_contains = (
    _SKILL_PROMPT,
    _FOLLOW_UP,
    f"Loaded skill: {_NAME}",
    "Review complete.",
    "! echo queued",
)
screen_excludes = ("Queued", "Ran echo")

# Turn one holds its completion until the skill is queued, its follow-up merged, and the shell refused.
timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    turn_completed(),
    {"release": 4},
    f"{_SKILL_PROMPT}\r",
    turn_started(),
    user_msg(f"{_SKILL_PROMPT}\n\n{_FOLLOW_UP}"),
    loaded_skill(_NAME, _BODY),
    assistant_msg("Review complete."),
    turn_completed(),
    f"{_FOLLOW_UP}\r",
    "!echo queued",
    "\r",
    {"release": 1},
    {"release": 6},
]
