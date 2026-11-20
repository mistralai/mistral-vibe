"""A group ending on a tool group closes its rail on that group's last row: folded in the first snapshot, unfolded at the end."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.turn_details._turns import HANDSHAKE, PROMPT

handshake = HANDSHAKE

_EXPAND = "\x1b[<0;5;11M\x1b[<0;5;11m"

timeline: Timeline = [
    f"{PROMPT}\r",
    turn_started(),
    user_msg(PROMPT),
    assistant_msg("Let me look at the test first."),
    bash("cat test_auth.py", "def test_login(): ..."),
    bash("pytest test_auth.py", "1 failed"),
    bash("git status", "clean"),
    assistant_msg("Fixed."),
    turn_completed(),
    _EXPAND,
    take_snapshot(),
    "\x1b[<0;5;15M\x1b[<0;5;15m",
    take_snapshot(),
]

screen_rows = {14: "│ ⏷ Ran 3 commands", 17: "└ └ ⏵ Ran git status", 18: "", 19: ""}
