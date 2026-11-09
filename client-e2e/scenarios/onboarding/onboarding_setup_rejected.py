"""`--setup` with a rejected submit-choices prints the provider-config warning."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

client_args = ("--setup",)
capture_startup = False
capture_steps = set()
exit_after_last_step = True
request_methods = {"setup/status", "setup/store-credential", "setup/submit-choices"}

# The server rejects the choices; the golden pins the setup/submit-choices
# request itself (the exit line's warning text is pinned by onboarding_fake_server).
handshake = {
    "setup/submit-choices": {
        "outcome": "provider_config_error",
        "failures": ["provider"],
    }
}

timeline: Timeline = [
    "\r",  # welcome -> theme
    "\x1b[B",  # highlight atom-one-dark; the loaded VIBE_THEME is ansi-dark
    "\r",  # theme -> auth method
    "\x1b[B\r",  # auth: Use an API key -> api key screen
    "test-key-123",  # the masked card fills and validates
    "\r",  # submit: the rejected choices surface the warning on the way out
]
