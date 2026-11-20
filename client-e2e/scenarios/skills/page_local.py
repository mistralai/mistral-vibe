"""A local skill's page leads with its description, then its status, source, scope and file in a label column, has no rows to step through, and Space switches it from there."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from e2e.app_server.skills import APPLIED, METHODS, at_file, installed, skill

_PATH = "/repo/.agents/skills/create-pr/SKILL.md"
_CREATE_PR = at_file(
    skill(
        "create-pr",
        scope="project",
        description="Creates a GitHub PR end to end and watches it until it merges.",
    ),
    _PATH,
)

handshake = {
    "skills/installed": [
        installed([_CREATE_PR]),
        installed([{**_CREATE_PR, "enabled": False}]),
    ],
    "skills/setEnabled": APPLIED,
}
request_methods = METHODS

timeline: Timeline = ["/skills\r", "\r", take_snapshot("page"), " "]

screen_contains = (
    "Status   ○ Disabled",
    "Scope    Project",
    _PATH,
    "Space enable  e edit  x delete  Esc back",
)
screen_excludes = ("Enabled  yes", "↑↓/jk navigate")
