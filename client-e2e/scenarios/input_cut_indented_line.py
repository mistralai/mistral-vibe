"""Cutting an indented line copies exactly that line, not a shifted window."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

expected_clipboard = "  b\n"

timeline: Timeline = ["a\n  b\n  c", "\x1b[A\x18"]
