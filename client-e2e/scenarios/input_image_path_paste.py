"""A pasted absolute image path becomes an `[Image #1]` placeholder."""

from __future__ import annotations

from e2e.app_server.events import paste
from e2e.app_server.scenario import Timeline

# Prompt preparation checks the file, so the composer needs no fixture on disk.
timeline: Timeline = [paste("/tmp/vibe_e2e_fixtures/pasted image.png")]
