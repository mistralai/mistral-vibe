"""The settled `ask_user_question` effect: one line, no foldable body."""

from __future__ import annotations

from e2e.app_server.events import (
    ask_user_question_effect,
    assistant_msg,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_QUESTION = "Which part of the repo should I focus on?"

screen_contains = {
    "rust": (f'Answered "{_QUESTION}" → vibe/core',),
    "python": (f'Answered "{_QUESTION}" → vibe/core',),
}
screen_excludes = {"rust": ("Asked questions",), "python": ("Asked questions",)}

timeline: Timeline = [
    "where should I look\r",
    turn_started(),
    user_msg("where should I look"),
    ask_user_question_effect(_QUESTION, "vibe/core"),
    assistant_msg("Focusing on vibe/core."),
    turn_completed(),
    # Clicking the row must not fold anything open: the result is not collapsible.
    "\x1b[<0;10;27M\x1b[<0;10;27m",
]
