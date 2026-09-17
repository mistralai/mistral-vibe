from __future__ import annotations

from e2e.app_server.scenario import Timeline

# The Rust-only build hint is hidden under the replay harness so it never breaks
# terminal parity with the Python CLI. This scenario forces it on so one golden
# locks its wording, color, and placement below the info block. Python has no
# equivalent line, so terminal parity is skipped and only the Rust screen is
# asserted. One Shift+Tab settles a frame with the banner still on screen (the
# golden harness never captures the startup frame); both clients emit the same
# agent-switch RPC, so request parity is unaffected.
_SHIFT_TAB = "\x1b[Z"

env = {"VIBE_TEST_SHOW_RUST_HINT": "1"}
skip_terminal_parity = "Rust-only build hint has no Python equivalent"
screen_contains = {"rust": ("You are using the Rust version of Vibe (experimental).",)}

timeline: Timeline = [_SHIFT_TAB]
