"""An editor that cannot start keeps the draft and says why."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_CTRL_G = "\x07"

env = {"VISUAL": "vibe-e2e-missing-editor --wait"}

capture_startup = False
screen_contains = {
    "rust": ("keep my draft", "Could not open editor vibe-e2e-missing-editor")
}

timeline: Timeline = ["keep my draft", _CTRL_G]
