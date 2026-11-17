"""A fake tmux on PATH whose session answers `mouse off`."""

from __future__ import annotations

import atexit
import os
from pathlib import Path
import shutil
import tempfile

_bin = tempfile.mkdtemp(prefix="e2e_fake_tmux_off_")
atexit.register(shutil.rmtree, _bin, True)
_tmux = Path(_bin, "tmux")
_tmux.write_text('#!/bin/sh\n[ "$1" = display-message ] && echo 0\n')
_tmux.chmod(0o755)

MOUSE_OFF_ENV = {
    "PATH": f"{_bin}{os.pathsep}{os.environ['PATH']}",
    "TMUX": "/tmp/vibe-e2e-tmux/default,1,0",
    "TMUX_PANE": "%1",
}
