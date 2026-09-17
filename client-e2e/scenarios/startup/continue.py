"""`--continue` resumes the most recent saved session at startup."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

# Known, deliberate divergence: Rust resolves the target and resumes inside the
# handshake, so its first settled frame already shows the continued session.
# Python resumes from a worker after startup and settles while still on
# "Resuming session…". Both clients end on the same transcript.
skip_terminal_parity = (
    "rust attaches the continued session during the handshake, python after it"
)

client_args = ("--continue",)
timeline: Timeline = []
