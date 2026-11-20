"""Leftovers the server keeps (one in use, one too large to check) are reported with why, grouped by reason."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.storage._fixtures import (
    REQUEST_METHODS,
    RETRY_WT,
    SCRATCH_DIR,
    SPIKE_WT,
    TARGET_DIR,
    flagged,
    usage,
)

handshake = flagged({
    "storage/usage/read": usage(suggested=[], leftovers=True),
    "storage/usage/refresh": {"started": False, "reason": "fresh"},
    "storage/leftovers/clean": {
        "removed": [SPIKE_WT, RETRY_WT],
        "kept": [
            {"id": TARGET_DIR, "reason": "in_use"},
            {"id": SCRATCH_DIR, "reason": "too_large_to_check"},
        ],
    },
})
request_methods = REQUEST_METHODS

timeline: Timeline = ["/storage", "\r", "\r", "\r", "y"]

screen_contains = (
    "Cleaned up 2 items. Freed about 760 MB.",
    "Kept 2 items: in use (target), too large to check that nothing uses it (scratch).",
    "Clean up 2 leftover folders  ·  frees about 2.1 GB →",
)
