"""Enter on the agents item opens the subagent list in the input box's place; Esc goes back to the rail, then the prompt."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.rail._fixtures import CTRL_X, ESCAPE, SETUP, handshake

__all__ = ["handshake"]

# Back in the prompt, `s` is typed instead of opening the list again.
timeline: Timeline = [*SETUP, CTRL_X, "\r", ESCAPE, ESCAPE, "s"]

screen_excludes = ("Main conversation",)
