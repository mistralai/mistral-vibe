"""Scenario: mouse drag-select the welcome banner's info block (fresh session).

On an empty session the banner is the only chat content, top-anchored: the
braille cat (frozen/blank at its starting pose under replay) with the info block
beside it -- `Mistral Vibe v<ver>`, the model line, and `Type /help ...`. Those
info rows sit in the chat area (chunks[0]), so a drag over them highlights
transcript cells, and the copy leaves the cat out. The version string is the
fixture's fixed mock serverInfo.version, so the rendered cells stay identical
across releases.

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
expected_clipboard = "Mistral Vibe v0.0.0-eval\nmistral-medium-3.5[medium]\nType /help for more information"

# The info block occupies 0-based chat rows 1-3 (SGR 2-4) from column 14, right
# of the cat: the version line, the model line, and the `Type /help` line. Drag
# from the start of the version line down to the `Type /help` line.
_PRESS = "\x1b[<0;14;2M"
_DRAG_MID = "\x1b[<32;40;3M"
_DRAG_END = "\x1b[<32;60;4M"
_RELEASE = "\x1b[<0;60;4m"

_SELECT = _PRESS + _DRAG_MID + _DRAG_END + _RELEASE

timeline: Timeline = [_SELECT]
