"""Paragraph wrapping: fold width, folded overlong words, and space runs."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_PROMPT = "wrap this"
_ANSWER = "\n".join([
    "This paragraph is long enough to fold across rows, it keeps  double  spaces, "
    "drops a <tag> while keeping the spaces around it, and ends near the boundary.",
    "",
    "M " + "a" * 113 + " b",
    "",
    "N " + "a" * 111 + " b",
    "",
    "L " + "x" * 200,
    "",
    "- item  with  spaces <x> here",
    "- second",
])

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
]
