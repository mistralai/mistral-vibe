from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

_SHIFT_TAB = "\x1b[Z"

# Four presses from `accept edits` walk every approval policy and wrap back:
# smart approve, auto approve, ask, accept edits. Agents never cycle.
timeline: Timeline = [_SHIFT_TAB, _SHIFT_TAB, _SHIFT_TAB, _SHIFT_TAB, take_snapshot()]
