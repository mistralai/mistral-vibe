"""Wrapped edit-diff rows break at word boundaries and hang under the gutter."""

from __future__ import annotations

from e2e.app_server.events import edit_file, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_PROMPT = "reword the paragraph"

_OLD = (
    "intro\n"
    "When text wraps in the diff view, the text is split at the end of the line, even in the "
    "middle of a word, which makes the change hard to read.\n"
    "outro\n"
)
_LONG_WORD = "unbreakable" * 12
_NEW = (
    "intro\n"
    "When text wraps in the diff view, the line break respects word separators and only breaks "
    f"inside a word when that word is longer than the line: {_LONG_WORD} end.\n"
    "outro\n"
)

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    edit_file("notes.md", [(40, _OLD, _NEW)]),
    turn_completed(),
    # Ctrl+O expands the lone folded edit so its wrapped diff rows render.
    "\x0f",
]
