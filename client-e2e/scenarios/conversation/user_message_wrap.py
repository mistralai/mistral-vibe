"""A wrapped user prompt indents its continuation rows under the text, past the `> ` marker."""

from __future__ import annotations

from e2e.app_server.events import paste, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_WORDS = " ".join(f"word{index:02d}" for index in range(1, 31))
_TOKEN = "x" * 150
_PROMPT = f"{_WORDS}\nshort line\n{_TOKEN}"

screen_contains = {"rust": ("\n  word18 word19", "\n  " + "x" * 32)}
screen_excludes = {"rust": ("\nword18", "\nxxx")}

timeline: Timeline = [
    paste(_PROMPT),
    "\r",
    turn_started(),
    user_msg(_PROMPT),
    turn_completed(),
]
