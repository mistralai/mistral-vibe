"""The VS Code extension promo shows once in a VS Code-family terminal."""

from __future__ import annotations

from e2e.app_server.scenario import Action, Timeline

# The promo needs a VS Code-family terminal; a fresh VIBE_HOME shows it once.
env = {"TERM_PROGRAM": "vscode"}

_URI = "vscode:extension/mistralai.mistral-vibe-code"
expected_actions = [Action("open_url", _URI)]

# `VS Code extension` spans one-based columns 17-33 on the promo row.
_CLICK = "\x1b[<0;20;5M\x1b[<0;20;5m"

# The promo mounts after startup; the capture after typing settles it.
capture_startup = False
timeline: Timeline = ["x", _CLICK]
