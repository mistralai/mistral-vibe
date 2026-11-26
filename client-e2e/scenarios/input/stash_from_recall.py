"""Ctrl+S on a recalled history entry stashes the typed draft, not the recalled prompt."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_CTRL_S = "\x13"
_HOME = "\x1b[H"
_UP = "\x1b[A"

screen_contains = ("> typed draft",)
screen_excludes = ("restore stash",)

timeline: Timeline = [
    "earlier prompt\r",
    turn_started(),
    user_msg("earlier prompt"),
    assistant_msg("ok"),
    turn_completed(),
    "typed draft",
    # Home then Up recalls the earlier prompt over the typed draft.
    _HOME,
    _UP,
    # Ctrl+S stashes the typed draft and empties the input; Ctrl+S again brings it back.
    _CTRL_S,
    _CTRL_S,
]
