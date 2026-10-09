"""A box selection is dropped once a key or the wheel changes the text under it."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}
capture_steps = {1, 2, 3, 4}
clipboard_clients = {"rust"}
expected_clipboard = "Log Level"

# Drag across the picker title (1-based SGR coordinates), then scroll down over the box.
_DRAG_TITLE = "\x1b[<0;3;29M\x1b[<32;60;29M\x1b[<0;60;29m"
_WHEEL_DOWN = "\x1b[<65;20;33M"

timeline: Timeline = ["/log-level\r", _DRAG_TITLE, "j", _DRAG_TITLE, _WHEEL_DOWN]
