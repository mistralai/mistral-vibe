"""A triple click on a code row in an expanded group copies the row, as outside a group."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.turn_details._turns import HANDSHAKE, markdown_turn

handshake = HANDSHAKE
env = {"SSH_TTY": "/dev/pts/0"}

_EXPAND = "\x1b[<0;5;11M\x1b[<0;5;11m"
# On `let` (column 12), in the code block's indented row (SGR 23).
_CLICK = "\x1b[<0;12;23M\x1b[<0;12;23m"

timeline: Timeline = [*markdown_turn(), _EXPAND, _CLICK * 3]

expected_clipboard = "let value = 1;"
