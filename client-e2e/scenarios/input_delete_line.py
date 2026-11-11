from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

_DELETE_LINE = "\x1b[107;6u"

timeline: Timeline = [
    "abcdef\nx\ny",
    f"\x1b[A\x1b[A\x05{_DELETE_LINE}Z",
    take_snapshot(),
]
