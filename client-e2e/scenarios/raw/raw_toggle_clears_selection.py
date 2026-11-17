"""Toggling `/raw` drops a held selection, since the rows under it change."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.raw._answer import ANSWERED, LONG

env = {"SSH_TTY": "/dev/pts/0"}
expected_clipboard = LONG.split(" then")[0]

_SELECT = "\x1b[<0;3;15M\x1b[<32;46;15M\x1b[<0;46;15m"

timeline: Timeline = [*ANSWERED, _SELECT, "/raw\r", take_snapshot("cleared")]
