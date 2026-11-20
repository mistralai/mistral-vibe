"""Copying across a collapsed group keeps its header row, like a tool group's, and none of its steps."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.turn_details._turns import HANDSHAKE, PROMPT, worked_turn

handshake = HANDSHAKE
env = {"SSH_TTY": "/dev/pts/0"}

# Drag from the prompt (SGR 8) across the header (SGR 11) to the final answer (SGR 13).
_SELECT = "\x1b[<0;1;8M\x1b[<32;60;13M\x1b[<0;60;13m"

timeline: Timeline = [*worked_turn(), _SELECT]

expected_clipboard = (
    f"{PROMPT}\nWorked for 1m 12s · 2 messages · 3 tool calls\n"
    "Fixed: the token now refreshes before the retry."
)
