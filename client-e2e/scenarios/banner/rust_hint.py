from __future__ import annotations

from e2e.app_server.scenario import Timeline

# Force the build hint on and settle a banner frame with Shift+Tab.
_SHIFT_TAB = "\x1b[Z"

env = {"VIBE_TEST_SHOW_RUST_HINT": "1"}
screen_contains = {
    "rust": (
        "Experimental Rust TUI - unset `VIBE_CLI` (or set it to `python`) for the legacy Python TUI.",
    )
}

timeline: Timeline = [_SHIFT_TAB]
