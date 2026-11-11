"""A drag across the preview beside the list copies the preview's text alone, never the rows beside it."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, resize
from e2e.app_server.skills import METHODS, installed, skill
from e2e.pty.mouse import drag

handshake = {
    "skills/installed": installed([
        skill("lint", prompt="# lint\n\nCheck the style before every commit."),
        skill("deploy"),
    ])
}
request_methods = METHODS
env = {"SSH_TTY": "/dev/pts/0"}
expected_clipboard = "# lint\n   Check the style before every commit."

_DRAG = drag((86, 28), (125, 30))

timeline: Timeline = [resize(40, 160), "/skills\r", _DRAG]
