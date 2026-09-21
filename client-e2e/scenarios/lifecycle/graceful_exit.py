"""A confirmed Ctrl+C exit stops the session and terminates cleanly."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

exit_after_last_step = True
capture_startup = False
capture_steps = set()
request_methods = {"session/stop"}

timeline: Timeline = ["\x03", "\x03"]
