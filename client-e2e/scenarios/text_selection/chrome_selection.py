"""A tool disclosure header remains selectable without expanding it."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    read_file,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}

_HEADER_DRAG = "\x1b[<0;8;30M\x1b[<32;29;30M\x1b[<0;29;30m"

timeline: Timeline = [
    "show chrome\r",
    turn_started(),
    user_msg("show chrome"),
    read_file("selection.txt", "visible tool result", num_lines=1),
    assistant_msg("Done."),
    turn_completed(),
    _HEADER_DRAG,
]
