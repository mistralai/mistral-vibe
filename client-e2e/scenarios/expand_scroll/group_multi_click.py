"""Every click on a group toggles it; only a drag across cells selects its text."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}

# The drag below selects "Ra" without folding the group the triple click left open.
expected_clipboard = "Ra"
screen_rows = {10: "⏷ Ran 3 commands", 11: "⎢ ⏵ Ran ls dir1"}

# SGR row 11 is the group header: the arrow at column 1, "Ran" from column 3.
_HEADER = 11


def _clicks(count: int) -> str:
    return f"\x1b[<0;5;{_HEADER}M\x1b[<0;5;{_HEADER}m" * count


timeline: Timeline = [
    "list files\r",
    turn_started(),
    user_msg("list files"),
    *(bash(f"ls dir{index}", f"file{index}.txt") for index in range(1, 4)),
    assistant_msg("Listed."),
    turn_completed(),
    # A double click unfolds then folds the group, selecting nothing.
    _clicks(2),
    # A triple click toggles three times and leaves it open.
    _clicks(3),
    f"\x1b[<0;3;{_HEADER}M\x1b[<32;4;{_HEADER}M\x1b[<0;4;{_HEADER}m",
]
