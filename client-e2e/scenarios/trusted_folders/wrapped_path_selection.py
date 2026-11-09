"""Wrapped trust paths keep their last column when selected in either direction."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, resize

env = {"SSH_TTY": "/dev/pts/0"}
expected_clipboard = (
    "/home/user/projects/abcdefghijklmnopqrstuvwxyz01"
    "23456789/another-long-directory/workspace/abcdef"
)

handshake = {
    "workspace/trust/status": {
        "status": "untrusted",
        "details": {
            "cwd": (
                "/home/user/projects/abcdefghijklmnopqrstuvwxyz0123456789/"
                "another-long-directory/workspace/abcdef"
            ),
            "repoRoot": None,
            "detectedFiles": [f"file{index:02d}.md" for index in range(20)],
            "repoDetectedFiles": [],
            "repoExplicitlyUntrusted": False,
            "settingsPath": "/home/user/.vibe/trusted_folders.toml",
            "availableDecisions": ["trust_cwd", "decline"],
        },
    }
}

# Both path rows fill the 48-column content area; only the file list has a gutter.
timeline: Timeline = [
    resize(40, 60),
    "\x1b[<0;7;23M\x1b[<32;54;24M\x1b[<0;54;24m",
    "\x1b[<0;54;24M\x1b[<32;7;23M\x1b[<0;7;23m",
]
