"""`d` twice on a saved session deletes it and leaves the picker on the rest."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_ESCAPE = "\x1b[27u"

# Up moves off the current session (the picker preselects it, and the current
# session cannot be deleted) onto the saved one.
timeline: Timeline = ["/resume", "\r", "\x1b[A", "d", "d", _ESCAPE]

capture_startup = False
# The 100 ms preview debounce can race the transient frame immediately after
# deletion under load. The final frame still verifies both deletion and cancel.
capture_steps = {1, 2, 3, 5}
settle_per_key = True
