"""Twenty Shift+Enter lines keep the composing caret visible."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

_SHIFT_ENTER = "\x1b[13;2u"
_LINE_COUNT = 20

_LINE_COUNT = 20

timeline: Timeline = []
for number in range(_LINE_COUNT):
    timeline.append(f"line {number}")
    if number < _LINE_COUNT - 1:
        timeline.append(_SHIFT_ENTER)
timeline.append(take_snapshot())
