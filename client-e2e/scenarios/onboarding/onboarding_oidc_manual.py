"""OIDC mode rejects manual API-key submission without storing a credential."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

client_args = ("--setup",)
exit_after_last_step = True
handshake = {"setup/status": {"oidcEnabled": True}}
request_methods = {
    "setup/status",
    "setup/oidc-start",
    "setup/oidc-poll",
    "setup/oidc-cancel",
    "setup/store-credential",
    "setup/submit-choices",
}
expected_actions = []
screen_contains = ("OIDC mode requires browser sign-in",)
timeline: Timeline = [
    "\r",
    "\r",
    "\x1b[B\r",
    "test-key-123",
    "\r",
    take_snapshot(),
    "\x03",
]
