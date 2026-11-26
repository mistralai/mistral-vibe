"""Startup issues, validation warnings and early server warnings share one framed box (VIBE-4099)."""

from __future__ import annotations

from e2e.app_server.events import server_warning
from e2e.app_server.scenario import Timeline

handshake = {
    "runtime/read": {
        "runtime": {
            "issues": [
                {
                    "file": ".vibe/skills/broken/SKILL.md",
                    "message": "Failed to load: bad frontmatter",
                },
                {"file": ".vibe/hooks.toml", "message": "Failed to parse: bad toml"},
            ],
            "config": {
                "validationWarnings": [
                    "Model 'toto' is not available — running 'Mistral Medium 3.5'."
                ]
            },
        }
    }
}

# A server warning sent while the session opens joins the box instead of mounting a row.
on_request = {
    "session/start": [
        server_warning(
            "MCP server 'github' failed to connect: timeout.",
            code="integrations_unavailable",
        )
    ]
}

timeline: Timeline = ["a"]

screen_contains = (
    "Warnings",
    "Failed to load: bad frontmatter",
    "Failed to parse: bad toml",
    "Model 'toto' is not available",
    "MCP server 'github' failed to connect: timeout.",
)
screen_excludes = ("⚠",)
