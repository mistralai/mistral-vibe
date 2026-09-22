"""The VS Code extension promo shows once in a VS Code-family terminal."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

# The promo needs a VS Code-family terminal; a fresh VIBE_HOME shows it once.
env = {"TERM_PROGRAM": "vscode"}

# The promo mounts after startup; the capture after typing settles it.
capture_startup = False
timeline: Timeline = ["x"]
