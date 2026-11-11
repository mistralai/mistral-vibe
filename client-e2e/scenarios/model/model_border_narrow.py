"""Narrowing drops the thinking level, then elides and hides the model while the mode stays whole; the gauge turns into a level."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, resize

timeline: Timeline = [resize(40, 60), resize(40, 44), resize(40, 34), resize(40, 26)]

screen_contains = ("─── accept edits ─", "/test/workdir     ctx   0%")
screen_excludes = (" · accept edits", "0/600k")
