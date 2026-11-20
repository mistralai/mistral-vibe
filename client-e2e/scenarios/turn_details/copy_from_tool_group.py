"""Copying from an expanded group's opening tool group leaves out the group's rail."""

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
env = {"SSH_TTY": "/dev/pts/0"}

_EXPAND = "\x1b[<0;5;11M\x1b[<0;5;11m"
# From the tool group header (SGR 13) to the final answer (SGR 16).
_SELECT = "\x1b[<0;1;13M\x1b[<32;60;16M\x1b[<0;60;16m"

timeline: Timeline = [
    f"{PROMPT}\r",
    turn_started(),
    user_msg(PROMPT),
    bash("cat test_auth.py", "def test_login(): ..."),
    bash("pytest test_auth.py", "1 passed"),
    assistant_msg("Fixed."),
    turn_completed(),
    _EXPAND,
    take_snapshot(),
    _SELECT,
]

expected_clipboard = "Ran 2 commands\nFixed."
