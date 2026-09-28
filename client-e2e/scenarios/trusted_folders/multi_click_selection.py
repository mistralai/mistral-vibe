"""Double- and triple-clicking trust text selects a word, then a whole row."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

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


def _clicks(column: int, row: int, count: int) -> str:
    return f"\x1b[<0;{column};{row}M\x1b[<0;{column};{row}m" * count


timeline: Timeline = [_clicks(60, 15, 2), _clicks(60, 16, 3)]
