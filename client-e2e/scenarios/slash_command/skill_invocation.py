"""A user-invoked skill keeps its literal prompt and renders a settled effect."""

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
_PROMPT = f"/{_NAME} please"
_BODY = f'<skill_content name="{_NAME}">\nReview twice.\n</skill_content>'
_MESSAGE_ID = "user-code-review"
_SKILLS = [
    {
        "name": _NAME,
        "description": "Review the current change.",
        "prompt": "",
        "userInvocable": True,
        "source": "local",
    }
]

handshake = {
    "runtime/read": {"runtime": {"skills": _SKILLS}},
    "workspace/prompt/prepare": {
        "prompt": {
            "displayText": _PROMPT,
            "promptText": _PROMPT,
            "images": [],
            "autoTitle": None,
            "mentions": {"count": 0, "contextTypes": {}, "fileExtensions": {}},
        }
    },
}

screen_contains = {
    "rust": (
        _PROMPT,
        "Loaded skills",
        f"Loaded skill: {_NAME}",
        "Review twice.",
        "<skill_content",
        "Review complete.",
    )
}

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT, entry_id=_MESSAGE_ID),
    loaded_skill(_NAME, _BODY, related_entry_id=_MESSAGE_ID),
    assistant_msg("Review complete."),
    turn_completed(),
    "\x0f",
]
