"""Narrowing the footer elides the model, then hides it, then elides the short cwd; the counter stays whole."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, resize

timeline: Timeline = [resize(40, 44), resize(40, 36), resize(40, 26)]

screen_contains = ("/test/… 0/600k tokens (0%)",)
