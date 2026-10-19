"""Resuming another session rebuilds the transcript and shows the custom-tools banner once again."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.conversation.custom_tools_deprecation import handshake as handshake

_UP = "\x1b[A"

request_methods = {"session/resume"}
# Up highlights the saved session above the current one; Enter resumes it.
timeline: Timeline = ["/resume", "\r", _UP, "\r"]

capture_startup = False
screen_contains = ("legacy_lint", "Saved session restored.")
