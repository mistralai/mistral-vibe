"""In `/raw`, dragging over a soft-wrapped paragraph copies it back as one source line."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.raw._answer import ANSWERED, LONG

env = {"SSH_TTY": "/dev/pts/0"}
expected_clipboard = LONG

_SELECT_PARAGRAPH = "\x1b[<0;1;13M\x1b[<32;20;14M\x1b[<0;20;14m"

timeline: Timeline = [*ANSWERED, "/raw\r", _SELECT_PARAGRAPH]
