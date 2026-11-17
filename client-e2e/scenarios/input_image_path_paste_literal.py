"""Pasting an image path again turns its `[Image #1]` placeholder back into the literal path."""

from __future__ import annotations

from e2e.app_server.events import paste
from e2e.app_server.scenario import Timeline, take_snapshot

_PATH = "/tmp/vibe_e2e_fixtures/pasted image.png"

screen_contains = (_PATH,)
screen_excludes = ("[Image #1]",)

timeline: Timeline = [paste(_PATH), take_snapshot(), paste(_PATH)]
