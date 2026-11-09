"""`--setup` custom-domain completion drifts the provider onto submit-choices."""

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
    "\r",  # auth: Launch browser -> sign-in target
    "\x1b[B\r",  # target: Other -> custom domain
    "my.example",  # type a domain: the submit hint appears
    "\r",  # apply the domain and start the replayed browser sign-in
    "m",  # switch to the API key screen, cancelling the sign-in
    "test-key-123",  # the masked card fills and validates
    "\r",  # submit: the drifted provider lands through submit-choices, then exit
]
