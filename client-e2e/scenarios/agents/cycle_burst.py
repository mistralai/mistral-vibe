from __future__ import annotations

from e2e.app_server.scenario import Timeline

_SHIFT_TAB = "\x1b[Z"

# Three presses inside one step: the switches coalesce onto the last target
# (`plan`) instead of queueing three round-trips.
timeline: Timeline = [_SHIFT_TAB * 3]
