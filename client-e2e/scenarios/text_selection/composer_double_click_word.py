"""A double-click in the composer selects the word under the pointer and copies it."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}

timeline: Timeline = [
    "a draft in the composer",
    "\x1b[<0;10;36M\x1b[<0;10;36m\x1b[<0;10;36M\x1b[<0;10;36m",
]
