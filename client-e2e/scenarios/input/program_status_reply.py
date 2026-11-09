"""Unsolicited detection replies are filtered without losing the following input."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

timeline: Timeline = ["\x1b]7501;?:future=ignored\x1b\\x"]
