"""A long paste collapses to `[Pasted n characters]`; pasting it again shows it in full."""

from __future__ import annotations

from e2e.app_server.events import paste
from e2e.app_server.scenario import Timeline

_TEXT = "\n".join(f"line {number}" for number in range(30))

screen_contains = {"rust": ("line 29",)}

# The second paste expands the placeholder, keeping the final line and caret visible.
timeline: Timeline = [paste(_TEXT), paste(_TEXT)]
