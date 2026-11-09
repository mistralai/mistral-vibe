"""Pasting slash-prefixed text into an empty composer opens slash completion."""

from __future__ import annotations

from e2e.app_server.events import paste
from e2e.app_server.scenario import Timeline

screen_contains = ("/help",)

timeline: Timeline = [paste("/he")]
