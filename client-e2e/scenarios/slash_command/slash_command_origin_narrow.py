"""A narrow `/` menu keeps a skill's origin at the head of its wrapped description."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, resize

_SKILLS = [
    {
        "name": "ci-status",
        "description": "Summarize the CI run.",
        "prompt": "",
        "userInvocable": True,
        "source": "local",
        "scope": "project",
    },
    {
        "name": "code-style",
        "description": "Apply the team style.",
        "prompt": "",
        "userInvocable": True,
        "source": "registry",
        "scope": "global",
    },
]

handshake = {"runtime/read": {"runtime": {"skills": _SKILLS}}}

capture_startup = False
screen_contains = ("project · Summarize", "user · Apply the")

timeline: Timeline = ["/c", resize(30, 32)]
