"""Dragging onto the next line after a triple press in the composer selects both whole lines."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}
expected_clipboard = "hello world\nbar baz"

_PRESS = "\x1b[<0;6;36M\x1b[<0;6;36m"
_DRAG = "\x1b[<0;6;36M\x1b[<32;6;37M\x1b[<0;6;37m"

# Ctrl+J inserts the newline without submitting.
timeline: Timeline = ["hello world\nbar baz", f"{_PRESS}{_PRESS}{_DRAG}"]
