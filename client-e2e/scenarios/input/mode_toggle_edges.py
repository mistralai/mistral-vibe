"""Backspace at the start leaves a mode keeping its text; typing over all the text opens one."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_SELECT_ALL = "\x1b[18~"

timeline: Timeline = ["/config", "\x01\x7f", f"{_SELECT_ALL}!", "ls\x01\x7f"]
