"""Skills that failed to load are listed under the others with their file and why; the highlight skips them."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from e2e.app_server.skills import METHODS, at_file, installed, skill

_BROKEN = "/repo/.vibe/skills/too-long/SKILL.md"
_REASON = "Failed to load: description is 1312 characters, the most is 1024"

handshake = {
    "skills/installed": installed(
        [at_file(skill("lint"), "/repo/.vibe/skills/lint/SKILL.md")],
        [{"file": _BROKEN, "message": _REASON}],
    )
}
request_methods = METHODS

timeline: Timeline = ["/skills\r", "j", take_snapshot()]

screen_contains = ("Failed to load", _BROKEN, "description is 1312 characters")
