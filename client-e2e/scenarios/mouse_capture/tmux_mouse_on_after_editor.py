"""Turning tmux's `mouse` on while in the Ctrl+G editor turns mouse reports on when the TUI resumes."""

from __future__ import annotations

import atexit
import os
from pathlib import Path
import shutil
import tempfile

from e2e.app_server.scenario import Timeline

_CTRL_G = "\x07"

# The fake tmux answers `mouse off` until the editor writes `1` to `$VIBE_HOME/tmux_mouse`.
_bin = tempfile.mkdtemp(prefix="e2e_fake_tmux_")
atexit.register(shutil.rmtree, _bin, True)
_tmux = Path(_bin, "tmux")
_tmux.write_text(
    '#!/bin/sh\n[ "$1" = display-message ] && { cat "$VIBE_HOME/tmux_mouse" 2>/dev/null || echo 0; }\n'
)
_tmux.chmod(0o755)

env = {
    "PATH": f"{_bin}{os.pathsep}{os.environ['PATH']}",
    "TMUX": "/tmp/vibe-e2e-tmux/default,1,0",
    "TMUX_PANE": "%1",
    "VISUAL": "sh -c 'echo 1 > \"$VIBE_HOME/tmux_mouse\"' editor",
}
capture_startup = False
mouse_tracking = True
timeline: Timeline = ["x", _CTRL_G]
