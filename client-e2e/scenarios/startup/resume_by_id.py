"""`--resume <id>` resumes a specific saved session at startup."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

# Known, deliberate divergence, as in `startup/continue`: Rust resumes inside
# the handshake, so it settles on the resumed session; Python resumes from a
# worker after startup and settles mid-resume.
skip_terminal_parity = (
    "rust attaches the resumed session during the handshake, python after it"
)

client_args = ("--resume", "00000000-0000-4000-8000-000000000003")
timeline: Timeline = []
