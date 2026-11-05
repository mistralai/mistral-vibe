"""A drag across the startup Warnings box copies the warnings without the box rails (VIBE-4099)."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_LONG = (
    'Failed to load: Invalid YAML frontmatter: mapping values are not allowed here in "", '
    "line 3, column 241: ... o a `pr-watch-<number>` subagent: a schedule in this session for ... ^"
)

handshake = {
    "runtime/read": {
        "runtime": {
            "issues": [
                {
                    "file": "/home/user/.agents/skills/create-pr/SKILL.md",
                    "message": _LONG,
                },
                {"file": ".vibe/hooks.toml", "message": "Failed to parse: bad toml"},
            ]
        }
    }
}

env = {"SSH_TTY": "/dev/pts/0"}
# The wrapped warning rejoins into one line; the bullets stay, the rails and their padding drop.
expected_clipboard = (
    "/home/user/.agents/skills/create-pr/SKILL.md: Failed to load: Invalid YAML frontmatter: mapping "
    'values are not allowed here in "", line 3, column 241: ... o a `pr-watch-<number>` subagent: '
    "a schedule in this session for ... ^\n• .vibe/hooks.toml: Failed to parse: bad toml"
)

# Drag from the first warning's first letter to past the last row's closing rail (1-based SGR).
_SELECT = "\x1b[<0;5;7M\x1b[<32;120;9M\x1b[<0;120;9m"

timeline: Timeline = ["a", _SELECT]
