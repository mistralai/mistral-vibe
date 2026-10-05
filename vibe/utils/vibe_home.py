from __future__ import annotations

import os
from pathlib import Path

# Stdlib-only: the `vibe` launcher imports this before choosing the Rust or Python TUI.
_DEFAULT_VIBE_HOME = Path.home() / ".vibe"


def get_vibe_home() -> Path:
    if vibe_home := os.getenv("VIBE_HOME"):
        return Path(vibe_home).expanduser().resolve()
    return _DEFAULT_VIBE_HOME
