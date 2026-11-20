"""A drag inside a table cell of an expanded group stays in that cell, as outside a group."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.turn_details._turns import HANDSHAKE, markdown_turn

handshake = HANDSHAKE
env = {"SSH_TTY": "/dev/pts/0"}

_EXPAND = "\x1b[<0;5;11M\x1b[<0;5;11m"
# From `Stay` in the Behavior cell (SGR 18) past the row's right edge.
_DRAG = "\x1b[<0;45;18M\x1b[<32;119;18M\x1b[<0;119;18m"

timeline: Timeline = [*markdown_turn(), _EXPAND, _DRAG, take_snapshot()]

expected_clipboard = "Stay selectable"
