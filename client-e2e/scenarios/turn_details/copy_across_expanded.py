"""Copying across an expanded group keeps its steps' text, not their indent."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.turn_details._turns import HANDSHAKE, worked_turn

handshake = HANDSHAKE
env = {"SSH_TTY": "/dev/pts/0"}

_EXPAND = "\x1b[<0;5;11M\x1b[<0;5;11m"
# From the second message (SGR 17) across the blank rows to the final answer (SGR 22).
_SELECT = "\x1b[<0;1;17M\x1b[<32;60;22M\x1b[<0;60;22m"

timeline: Timeline = [*worked_turn(), _EXPAND, _SELECT]

expected_clipboard = (
    "The token expires during the retry. Patching it.\n"
    "Ran pytest test_auth.py\n"
    "Fixed: the token now refreshes before the retry."
)
