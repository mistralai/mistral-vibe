"""Emacs Ctrl+P / Ctrl+N move between composer lines and Ctrl+B / Ctrl+F by one character."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_CTRL_P = "\x10"
_CTRL_N = "\x0e"
_CTRL_F = "\x06"
_CTRL_B = "\x02"

timeline: Timeline = [
    "abcd\nefghij",
    _CTRL_P + _CTRL_B + _CTRL_B + "|",
    _CTRL_N + _CTRL_F + "^",
]
screen_contains = ("ab|cd", "efgh^ij")
