from __future__ import annotations

from e2e.app_server.scenario import Timeline

handshake = {
    "workspace/trust/status": {
        "status": "untrusted",
        "details": {
            "cwd": "/home/user/untrusted",
            "repoRoot": None,
            "detectedFiles": [f"file{i}.md" for i in range(12)],
            "repoDetectedFiles": [],
            "repoExplicitlyUntrusted": False,
            "settingsPath": "/home/user/.vibe/trusted_folders.toml",
            "availableDecisions": ["trust_cwd", "decline"],
        },
    }
}

# Twelve files overflow the 10-row scroll region: it grows a scrollbar and the
# text re-wraps one column narrower. Down scrolls that region by one row, and
# holding it reaches the last file, which the clipped region cannot show.
timeline: Timeline = ["\x1b[B", "\x1b[B" * 11]
