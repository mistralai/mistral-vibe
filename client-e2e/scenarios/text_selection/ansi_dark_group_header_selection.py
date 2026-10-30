"""ANSI-dark selection over a tool group header uses the same background as body rows."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0", "VIBE_THEME": "ansi-dark"}
handshake = {
    "config/read": {"config": {"theme": "ansi-dark"}},
    "runtime/read": {"runtime": {"config": {"theme": "ansi-dark"}}},
}
screen_rows = {10: "⏵ Ran 2 commands"}

# Drag from the group header (SGR row 11) down into the answer (row 13).
_DRAG = "\x1b[<0;1;11M\x1b[<32;8;13M\x1b[<0;8;13m"

timeline: Timeline = [
    "list files\r",
    turn_started(),
    user_msg("list files"),
    *(bash(f"ls dir{index}", f"file{index}.txt") for index in range(1, 3)),
    assistant_msg("Listed the files."),
    turn_completed(),
    _DRAG,
]
