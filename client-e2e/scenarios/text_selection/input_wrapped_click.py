"""Clicking a wrapped composer row moves the caret to that visual row."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_TEXT = "0123456789 " * 12
_CLICK_SECOND_ROW = "\x1b[<0;8;37M\x1b[<0;8;37m"

timeline: Timeline = [_TEXT, _CLICK_SECOND_ROW, "X"]
