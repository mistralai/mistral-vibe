"""OIDC mode rejects manual API-key submission without storing a credential."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

client_args = ("--setup",)
capture_startup = False
capture_steps = {4}
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
timeline: Timeline = ["\r", "\r", "\x1b[B\r", "test-key-123", "\r", "\x03"]
