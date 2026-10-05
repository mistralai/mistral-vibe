"""Before the turn starts, the Ctrl+C hint follows the prompts shown as queued."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
capture_startup = False
settle_per_key = True
capture_steps = {1, 2}

# Step 1 offers Ctrl+C for `second`; once it is removed, the unstarted first prompt is interruptible.
timeline: Timeline = ["first\r", "second\r", "\x03"]

screen_contains = {"rust": ("Esc/Ctrl+C to interrupt",)}
screen_excludes = {"rust": ("> second", "cancel last queued message")}
