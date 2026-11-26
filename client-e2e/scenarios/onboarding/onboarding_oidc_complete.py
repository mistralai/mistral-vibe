"""OIDC completes through submit-choices without credentials on the client wire."""

from __future__ import annotations

from e2e.app_server.scenario import Action, Timeline, advance, take_snapshot

client_args = ("--setup",)
exit_after_last_step = True
request_methods = {
    "setup/status",
    "setup/oidc-start",
    "setup/oidc-poll",
    "setup/oidc-cancel",
    "setup/store-credential",
    "setup/submit-choices",
}

_AUTHORIZATION_URL = "https://auth.mistral.ai/authorize?state=replay"
handshake = {
    "setup/status": {"oidcEnabled": True},
    "setup/oidc-start": {"attemptId": "oidc-replay"},
    "setup/oidc-poll": {"status": "completed", "authorizationUrl": _AUTHORIZATION_URL},
}
expected_actions = [Action("open_url", _AUTHORIZATION_URL)]

timeline: Timeline = [
    "\r",  # welcome -> theme
    "\x1b[B",  # select atom-one-dark
    "\r",  # theme -> auth method
    "\r",  # browser -> target
    "\r",
    take_snapshot(),  # Mistral AI -> OIDC: "Sign-in complete"
    advance(2000),  # the success dwell ends: choices, then session
]
