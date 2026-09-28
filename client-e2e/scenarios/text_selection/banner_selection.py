"""Scenario: mouse drag-select the welcome banner's info block (fresh session).

On an empty session the banner is the only chat content, bottom-anchored: the
braille cat (frozen/blank at its starting pose under replay) then the info block
-- `Mistral Vibe v<ver> ...`, the counts line, and `Type /help ...`. Those info
rows sit in the chat area (chunks[0]), so a drag over them highlights transcript
cells on both frontends. The version string is the fixture's fixed mock
serverInfo.version, so the rendered cells stay identical across releases.

No turn: a bare keystroke sequence -> a single settled frame once quiescent.

SGR mouse reports (1-based col;row), left button = 0:
- `\\x1b[<0;C;RM`   left-button press at (C, R)
- `\\x1b[<32;C;RM`  motion with left held (button 0 + 32 = drag)
- `\\x1b[<0;C;Rm`   left-button release (lowercase `m`)
"""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

# Force the OSC 52 copy path so the copy notice is deterministic across hosts.
env = {"SSH_TTY": "/dev/pts/0"}

# The info block occupies 0-based chat rows 28-30 (SGR 29-31): the version line,
# the counts line, and the `Type /help` line. Drag from the start of the version
# line down to the `Type /help` line for a multi-row banner selection.
_PRESS = "\x1b[<0;1;29M"
_DRAG_MID = "\x1b[<32;40;30M"
_DRAG_END = "\x1b[<32;60;31M"
_RELEASE = "\x1b[<0;60;31m"

_SELECT = _PRESS + _DRAG_MID + _DRAG_END + _RELEASE

timeline: Timeline = [_SELECT]
