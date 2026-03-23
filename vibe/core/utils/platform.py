from __future__ import annotations

import sys


def is_windows() -> bool:
    return sys.platform == "win32"
