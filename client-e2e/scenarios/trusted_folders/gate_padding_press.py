from __future__ import annotations

from e2e.app_server.scenario import Timeline

# Force the OSC 52 copy path so the copy is deterministic across hosts.
env = {"SSH_TTY": "/dev/pts/0"}
clipboard_clients = {"rust"}
expected_clipboard = "• AGENTS.md"
capture_steps = {1}

handshake = {
    "workspace/trust/status": {
        "status": "untrusted",
        "details": {
            "cwd": "/home/user/untrusted",
            "repoRoot": None,
            "detectedFiles": ["AGENTS.md", ".vibe/"],
            "repoDetectedFiles": [],
            "repoExplicitlyUntrusted": False,
            "settingsPath": "/home/user/.vibe/trusted_folders.toml",
            "availableDecisions": ["trust_cwd", "decline"],
        },
    }
}

# Start in the centering padding left of "• AGENTS.md" and drag across the row.
# The Rust gate treats the whole text column as a selection handle. SGR coordinates are 1-based.
_PRESS = "\x1b[<0;32;15M"
_DRAG_MID = "\x1b[<32;60;15M"
_DRAG_END = "\x1b[<32;80;15M"
_RELEASE = "\x1b[<0;80;15m"
# Clear the highlight after autocopy so final terminal parity still compares.
_CLEAR = "\x1b[<0;1;2M\x1b[<0;1;2m"

timeline: Timeline = [_PRESS + _DRAG_MID + _DRAG_END + _RELEASE, _CLEAR]
