"""The `/` completion filters on the word up to the caret, not the whole word."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_LEFT = "\x1b[D"

settle_per_key = True

# The popup includes /paste-image on macOS but not on Linux (cfg(target_os)),
# so the item count and popup height differ by platform.
skip = "popup item count differs by OS (/paste-image is macOS-only)"

timeline: Timeline = ["/status", _LEFT, _LEFT, _LEFT]

capture_startup = False
