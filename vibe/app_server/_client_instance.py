"""The client instance a connection comes from (`initialize`'s `clientInstance`).

Several clients can share one Vibe home, such as two dev Desktops on one
machine; their instance tells them apart. Features that keep state per instance
decide what to key on it.
"""

from __future__ import annotations

from pathlib import PureWindowsPath
import re
from typing import Final

# Mirrors `resolveDevInstanceProfile` in
# ts/apps/le-chat-desktop/src/main/core/dev-instance-profile.ts.
_DEV_DESKTOP_COPY: Final = re.compile(r".+-port-\d+")


def is_dev_desktop_copy(instance: str) -> bool:
    """Desktop's dev side-by-side profile copy, which Desktop deletes on quit:
    state that must last should not be keyed on it alone. Other clients' and
    installed Desktops' instances are not expected to match.
    """
    # Windows paths parse on every OS; POSIX ones have no backslash.
    return _DEV_DESKTOP_COPY.fullmatch(PureWindowsPath(instance).name) is not None
