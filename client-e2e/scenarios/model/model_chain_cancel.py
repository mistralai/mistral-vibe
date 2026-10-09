from __future__ import annotations

from e2e.app_server.scenario import Timeline

_ESCAPE = "\x1b[27u"

# /model, Enter picks a model and opens /thinking at once; Esc cancels the whole
# workflow, so nothing is written.
timeline: Timeline = ["/model", "\r", "\x1b[B", "\r", _ESCAPE]
