"""A click on the final answer under a folded group that opens on a tool group unfolds nothing."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline
from scenarios.turn_details._turns import HANDSHAKE, PROMPT

handshake = HANDSHAKE

# The final answer's first row lands on SGR row 13, under the header on SGR row 11.
_CLICK = "\x1b[<0;5;13M\x1b[<0;5;13m"

timeline: Timeline = [
    f"{PROMPT}\r",
    turn_started(),
    user_msg(PROMPT),
    bash("cat test_auth.py", "def test_login(): ..."),
    bash("pytest test_auth.py", "1 passed"),
    assistant_msg("Fixed."),
    turn_completed(),
    _CLICK,
]

screen_rows = {10: "⏵ Worked for <1s · 2 tool calls", 12: "  Fixed."}
screen_excludes = ("Ran 2 commands", "cat test_auth.py")
