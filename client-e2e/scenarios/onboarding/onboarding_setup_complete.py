"""`--setup` completion without drift persists the selected theme via submit-choices."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

client_args = ("--setup",)
capture_startup = False
capture_steps = set()
exit_after_last_step = True
request_methods = {"setup/status", "setup/store-credential", "setup/submit-choices"}

timeline: Timeline = [
    "\r",  # welcome -> theme
    "\x1b[B",  # highlight atom-one-dark; the loaded VIBE_THEME is ansi-dark
    "\r",  # theme -> auth method
    "\x1b[B\r",  # auth: Use an API key -> api key screen
    "test-key-123",  # the masked card fills and validates
    "\r",  # submit: store-credential, then the theme rides submit-choices
]
