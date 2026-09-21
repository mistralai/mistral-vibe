"""Moving the multiline caret above later rows remains safe to render."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_SHIFT_ENTER = "\x1b[13;2u"
_UP = "\x1b[A"

timeline: Timeline = [f"first{_SHIFT_ENTER}second{_SHIFT_ENTER}third", _UP]

# Each key must land on a settled UI: batching changes the outcome here.
settle_per_key = True
