"""A project directory query lists the directory itself first; Enter keeps it and closes the popup."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

expected_clipboard = "@at_popup/ ready"

timeline: Timeline = ["@at_popup/", "\r", " ready\x1b[18~\x19"]
