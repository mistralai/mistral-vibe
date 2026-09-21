from __future__ import annotations

from e2e.app_server.scenario import Timeline

# Force the OSC 52 copy path so the copy is deterministic across hosts.
env = {"SSH_TTY": "/dev/pts/0"}

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

# Drag the whole dialog, from "cious" in the warning down to the settings path:
# the gate's text is selectable even though the gate answers only keys. SGR
# coordinates are 1-based.
_PRESS = "\x1b[<0;36;11M"
_DRAG_MID = "\x1b[<32;60;20M"
_DRAG_END = "\x1b[<32;79;29M"
_RELEASE = "\x1b[<0;79;29m"

timeline: Timeline = [_PRESS + _DRAG_MID + _DRAG_END + _RELEASE]
