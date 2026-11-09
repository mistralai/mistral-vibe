"""A MissingApiKey session/start opens the wizard; the same child serves the session."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

# The probe-positive boot (the harness sets MISTRAL_API_KEY=fake-key) skips
# setup/status entirely; the handshake's unauthorized session/start raises the
# live missing-key verdict, and the respawn round's status seeds the wizard.
# The wizard round's fresh child stores the key first, and a completed
# store-credential retires the pinned error on that connection, so the retried
# session/start on the SAME child reaches Ready.
handshake = {
    "session/start": {
        "error": {
            "code": "unauthorized",
            "message": "Authentication is required for provider: mistral",
            "data": {"provider": "mistral", "env_key": "MISTRAL_API_KEY"},
        }
    }
}
request_methods = {
    "session/start",
    "setup/status",
    "setup/store-credential",
    "setup/submit-choices",
}

timeline: Timeline = [
    "\r",  # welcome -> theme
    "\r",  # theme -> auth method
    "\x1b[B\r",  # auth: Use an API key -> api key screen
    "test-key-123",  # the masked card fills and validates
    "\r",  # submit: the retried handshake reaches a fresh Ready
]
