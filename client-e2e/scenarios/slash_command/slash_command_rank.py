"""Typing /mcp must rank the exact command above fuzzy skill matches."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_SKILLS = [
    {
        "name": "compare-competitors",
        "description": "Compare Vibe's approach against competing CLI coding agents.",
        "prompt": "",
        "userInvocable": True,
        "source": "local",
    },
    {
        "name": "review-pr",
        "description": "Walk through the current PR one change at a time.",
        "prompt": "",
        "userInvocable": True,
        "source": "local",
    },
]

handshake = {"runtime/read": {"runtime": {"skills": _SKILLS}}}

# Rust keeps backticks in the /mcp menu description; python strips them.
skip_terminal_parity = (
    "rust keeps backticks in the /mcp menu description; python strips them"
)

timeline: Timeline = ["/mcp", "/", "/re", "/conf"]
