"""Skills mentioned with `/` keep the literal prompt and render one settled effect each."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    loaded_skill,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "use /lint and /code-review please"
_MESSAGE_ID = "user-skill-mentions"
_SKILLS = [
    {
        "name": name,
        "description": description,
        "prompt": "",
        "userInvocable": True,
        "source": "local",
    }
    for name, description in (
        ("code-review", "Review the current change."),
        ("lint", "Run the linters."),
    )
]


def _body(name: str, text: str) -> str:
    return f'<skill_content name="{name}">\n{text}\n</skill_content>'


handshake = {
    "runtime/read": {"runtime": {"skills": _SKILLS}},
    "workspace/prompt/prepare": {
        "prompt": {
            "displayText": _PROMPT,
            "promptText": _PROMPT,
            "images": [],
            "autoTitle": None,
            "mentions": {
                "count": 2,
                "contextTypes": {"skill": 2},
                "fileExtensions": {},
            },
        }
    },
}

screen_contains = {
    "rust": (
        _PROMPT,
        "Loaded 2 skills",
        "Loaded skill: lint",
        "Loaded skill: code-review",
        "Lint first.",
        "Review twice.",
        "Both applied.",
    )
}

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT, entry_id=_MESSAGE_ID),
    loaded_skill("lint", _body("lint", "Lint first."), related_entry_id=_MESSAGE_ID),
    loaded_skill(
        "code-review",
        _body("code-review", "Review twice."),
        related_entry_id=_MESSAGE_ID,
    ),
    assistant_msg("Both applied."),
    turn_completed(),
    "\x0f",
]
