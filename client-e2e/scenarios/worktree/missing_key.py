"""A worktree run whose replayed missing-key verdict runs the wizard, then the session."""

from __future__ import annotations

from e2e.app_server.events import worktree_settled
from e2e.app_server.scenario import Timeline

client_args = ("--worktree", "feature")
capture_startup = False
capture_steps = {4}

# The gate's handshake buffers the missing-key verdict; the wizard round's
# fresh child stores the key, retiring the pinned unauthorized on that
# connection, so the retried handshake settles the worktree on the SAME
# child and the chat opens in it.
handshake = {
    "session/start": {
        "error": {
            "code": "unauthorized",
            "message": "Authentication is required for provider: mistral",
            "data": {"provider": "mistral", "env_key": "MISTRAL_API_KEY"},
        }
    }
}
on_request = {
    "session/start": [
        worktree_settled(
            cwd="/home/user/worktrees/feature",
            name="feature",
            branch="feature",
            path="/home/user/worktrees/feature",
            created=True,
        )
    ]
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
    "\r",  # submit: the retried handshake settles the worktree, chat opens
]
