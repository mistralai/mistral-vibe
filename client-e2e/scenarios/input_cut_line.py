from __future__ import annotations

from e2e.app_server.scenario import Timeline

expected_clipboard = "abcdef\n"

timeline: Timeline = ["abcdef\nx\ny", "\x1b[A\x1b[A\x05\x18Z"]
