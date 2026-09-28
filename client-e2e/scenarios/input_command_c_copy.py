"""Command+C copies a selected prompt without replacing it with `c`."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

expected_clipboard = "hello"

_SELECT_ALL = "\x1b[18~"
_COMMAND_C = "\x1b[99;9u"

timeline: Timeline = ["hello", _SELECT_ALL, _COMMAND_C]
