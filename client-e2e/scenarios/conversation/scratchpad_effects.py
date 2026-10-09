"""Scratchpad writes, reads, and lists render as file bodies, never the raw MCP envelope."""

from __future__ import annotations

from e2e.app_server.effect_fixtures import effect_settled, effect_started
from e2e.app_server.events import turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_PROMPT = "take notes"
_FIXTURE = "scratchpad_effects.json"

screen_contains = {
    "rust": (
        "Wrote findings.md",
        "- Rust renders the note like a written file",
        "Read plan.py",
        "return 'it'",
        "Listed 2 files",
        "research/api-notes.md",
    )
}
screen_excludes = {
    "rust": ("structured_content", "_meta", "approvalSource", '"verb"', "files: ")
}

# Ctrl+O unfolds the group and every result body at once.
timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    *(
        event
        for name in ("write", "read", "list")
        for event in (effect_started(_FIXTURE, name), effect_settled(_FIXTURE, name))
    ),
    turn_completed(),
    "\x0f",
]
