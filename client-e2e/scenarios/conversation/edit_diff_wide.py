from __future__ import annotations

from e2e.app_server.events import edit_file, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

# Under tool-call folding the expanded diff rows render inside the group's
# `⎢` member gutter, so a wrapped continuation row carries both gutters.
screen_contains = {"rust": (f"  ⎢   ⎢ {' ' * 8}{'x' * 38}!",)}
screen_excludes = {"rust": ("▉", "▊", "▋", "▌", "▍", "▎", "▏")}

_PROMPT = "extend the long line"

_LONG = "x" * 140
_OLD = f"before\n{_LONG}\nafter\n"
_NEW = f"before\n{_LONG}!\nafter\n"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    edit_file("wide.txt", [(10000, _OLD, _NEW)]),
    turn_completed(),
    # The edit folds into a collapsed tool group; Ctrl+O expands it so the
    # wrapped diff rows render for the screen checks below.
    "\x0f",
]
