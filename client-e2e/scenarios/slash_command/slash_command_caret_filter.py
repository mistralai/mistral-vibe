"""The `/` completion filters on the word up to the caret, not the whole word."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_LEFT = "\x1b[D"

settle_per_key = True

timeline: Timeline = ["/status", _LEFT, _LEFT, _LEFT]

capture_startup = False
