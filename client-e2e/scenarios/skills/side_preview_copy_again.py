"""After a copy from the preview beside the list, a drag across the list copies its rows alone."""

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
expected_clipboard = "lint    Global  Local\n   ● deploy  Global  Local"

_DRAG_PREVIEW = drag((86, 28), (125, 30))
_DRAG_LIST = drag((8, 29), (125, 30))

timeline: Timeline = [resize(40, 160), "/skills\r", _DRAG_PREVIEW, _DRAG_LIST]
