"""Markdown structures wrap inside their own prefixes instead of scrolling sideways."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

screen_contains = {"rust": ("    list-tail", "  ▌ quote-tail", "   code-tail")}

_PROMPT = "wrap every markdown structure"
_GRAPHEME = "\u2764\ufe0f"
_ANSWER = "\n\n".join([
    f"- {'界' * 57} list-tail",
    f"> {_GRAPHEME * 57} quote-tail",
    f"```\n{'界' * 58} code-tail\n```",
])

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
]
