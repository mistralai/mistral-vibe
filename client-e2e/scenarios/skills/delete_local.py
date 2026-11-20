"""x on a local skill asks first, in place of the rows, and Delete then removes its folder by the file the server lists."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from e2e.app_server.skills import APPLIED, METHODS, at_file, installed, skill

_LINT = at_file(skill("lint", scope="project"), "/repo/.vibe/skills/lint/SKILL.md")

handshake = {
    "skills/installed": [
        installed([_LINT, skill("deploy")]),
        installed([skill("deploy")]),
    ],
    "skills/deleteLocal": APPLIED,
}
request_methods = METHODS

_RIGHT = "\x1b[C"

timeline: Timeline = ["/skills\r", "j", "x", take_snapshot("confirm"), _RIGHT, "\r"]

screen_contains = ("deploy",)
screen_excludes = ("lint", "Delete the skill")
