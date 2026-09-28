from __future__ import annotations

from e2e.app_server.scenario import Timeline

_SHIFT_TAB = "\x1b[Z"

# Four presses from `accept edits` walk every primary agent and wrap back:
# auto approve, ask, plan, accept edits. Subagents (`explore`) never appear.
timeline: Timeline = [_SHIFT_TAB, _SHIFT_TAB, _SHIFT_TAB, _SHIFT_TAB]
