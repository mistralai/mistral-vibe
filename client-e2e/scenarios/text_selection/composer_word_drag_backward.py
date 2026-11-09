"""Dragging left after a double press in the composer keeps the anchor word and snaps to whole words."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}
expected_clipboard = "hello world"

_PRESS = "\x1b[<0;12;36M\x1b[<0;12;36m"
_DRAG = "\x1b[<0;12;36M\x1b[<32;6;36M\x1b[<0;6;36m"

timeline: Timeline = ["hello world foo", f"{_PRESS}{_DRAG}"]
