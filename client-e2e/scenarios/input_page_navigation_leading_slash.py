"""A literal leading slash stays in the wrapped prompt during page navigation."""

from __future__ import annotations

from e2e.app_server.events import paste
from e2e.app_server.scenario import Timeline

_PAGE_DOWN = "\x1b[6~"
_BODY = "abcde" * 30

timeline: Timeline = [paste(_BODY) + "\x01/", f"\x01{_PAGE_DOWN}", "X"]
