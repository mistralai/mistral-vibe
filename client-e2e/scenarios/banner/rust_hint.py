from __future__ import annotations

from e2e.app_server.scenario import Timeline

# Force the build hint on and settle a banner frame with Shift+Tab.
_SHIFT_TAB = "\x1b[Z"

env = {"VIBE_TEST_SHOW_RUST_HINT": "1"}
screen_contains = {
    "rust": (
        "You are using the new Vibe TUI. To switch back to the classic TUI, set the `VIBE_CLI` environment variable to `python`.",
    )
}

timeline: Timeline = [_SHIFT_TAB]
