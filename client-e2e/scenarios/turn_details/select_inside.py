"""Selecting inside an expanded group copies the steps' text without their indent, as outside a group."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.turn_details._turns import HANDSHAKE, markdown_turn

handshake = HANDSHAKE
env = {"SSH_TTY": "/dev/pts/0"}

_EXPAND = "\x1b[<0;5;11M\x1b[<0;5;11m"
# Drag from the notes row (SGR 13) to the end of the code block (SGR 24).
_SELECT = "\x1b[<0;1;13M\x1b[<32;119;24M\x1b[<0;119;24m"

timeline: Timeline = [*markdown_turn(), _EXPAND, _SELECT, take_snapshot()]

_TABLE = "\n".join([
    "┌" + "─" * 38 + "┬" + "─" * 73 + "┐",
    "│ Feature" + " " * 30 + "│ Behavior" + " " * 64 + "│",
    "├" + "─" * 38 + "┼" + "─" * 73 + "┤",
    "│ Cells" + " " * 32 + "│ Stay selectable" + " " * 57 + "│",
    "└" + "─" * 38 + "┴" + "─" * 73 + "┘",
])
# A table copies with its frame, as outside a group; a code block copies its source.
expected_clipboard = (
    f"Some notes first.\n\n{_TABLE}\n\nfn main() {{\n    let value = 1;\n}}"
)
