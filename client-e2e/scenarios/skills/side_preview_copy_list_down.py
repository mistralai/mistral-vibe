"""A drag down the list beside a longer preview copies the list's rows, not the blank rows beside the preview's text."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, resize
from e2e.app_server.skills import METHODS, installed, skill
from e2e.pty.mouse import drag

_PROMPT = "# lint\n\nCheck the style before every commit.\n\nRun it on every file you changed.\n\nFix what it reports."

handshake = {
    "skills/installed": installed([skill("lint", prompt=_PROMPT), skill("deploy")])
}
request_methods = METHODS
env = {"SSH_TTY": "/dev/pts/0"}
expected_clipboard = "lint    Global  Local\n   ● deploy  Global  Local"

_DRAG_LIST = drag((8, 29), (60, 34))

timeline: Timeline = [resize(40, 160), "/skills\r", _DRAG_LIST]
