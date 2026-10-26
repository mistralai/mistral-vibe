"""Terminal key aliases: Ctrl+H, Ctrl+Alt+H, Alt+D, Alt+Enter and Ctrl+M edit the composer."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_CTRL_H = "\x08"
_CTRL_ALT_H = "\x1b\x08"
_ALT_D = "\x1bd"
_ALT_ENTER = "\x1b\r"
_CTRL_M = "\x1b[109;5u"
_HOME = "\x01"

timeline: Timeline = [
    "abcx",
    _CTRL_H,
    _ALT_ENTER,
    "def ghi",
    _CTRL_ALT_H,
    _CTRL_M,
    "jkl mno",
    _HOME,
    _ALT_D,
]

capture_steps = {1, 3, 4, 6, 8}
