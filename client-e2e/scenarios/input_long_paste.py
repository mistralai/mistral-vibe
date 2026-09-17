"""A long pasted draft keeps its final line and caret visible."""

from __future__ import annotations

from e2e.app_server.events import paste
from e2e.app_server.scenario import Timeline

timeline: Timeline = [paste("\n".join(f"line {number}" for number in range(30)))]
