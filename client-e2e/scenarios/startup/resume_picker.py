"""`--resume` with no id opens the saved-session picker at startup."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

# Known, deliberate divergence: the Rust client reads `session/list` before
# `session/start` and paints the picker (and its preview) while the engine is
# still coming up, with no `Initializing…` spinner. Python opens the picker from
# a worker only once the session is ready, so the startup captures disagree.
skip_terminal_parity = (
    "rust paints the --resume picker before the engine is ready, python after"
)

client_args = ("--resume",)
timeline: Timeline = []
