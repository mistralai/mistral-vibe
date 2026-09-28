"""Dragging at the trust file-list edge scrolls while extending selection."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}
clipboard_clients = {"rust"}
expected_clipboard = """\
fy AI behavior, exfiltrate data, run destructive commands, or silently alter your code.
Detected in current folder:
• file00.md
• file01.md
• file02.md
• file03.md
• file04.md
• file05.md
• file06.md
• file07.md
• file08.md
• file09.md
• file10.md
• file11.md
• file12.md
• file13.md
• file14.md
• file15.md
• file16.md
• file17.md
• file18.md
• file19.md"""

handshake = {
    "workspace/trust/status": {
        "status": "untrusted",
        "details": {
            "cwd": "/home/user/untrusted",
            "repoRoot": "/home/user",
            "detectedFiles": [f"file{index:02d}.md" for index in range(20)],
            "repoDetectedFiles": [],
            "repoExplicitlyUntrusted": False,
            "settingsPath": "/home/user/.vibe/trusted_folders.toml",
            "availableDecisions": ["trust_repo", "trust_cwd", "decline"],
        },
    }
}

_PRESS = "\x1b[<0;60;14M"
_DRAG = "\x1b[<32;60;19M"
_RELEASE = "\x1b[<0;60;19m"
_PRESS_UP = "\x1b[<0;60;18M"
_DRAG_UP = "\x1b[<32;60;9M"
_RELEASE_UP = "\x1b[<0;60;9m"

timeline: Timeline = [_PRESS, _DRAG, _RELEASE, _PRESS_UP, _DRAG_UP, _RELEASE_UP]
