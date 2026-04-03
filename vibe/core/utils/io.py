from __future__ import annotations

from pathlib import Path

import anyio


def read_safe(path: Path, *, raise_on_error: bool = False) -> str:
    """Read a text file trying UTF-8 first, falling back to OS-default encoding.

    On fallback, undecodable bytes are replaced with U+FFFD (REPLACEMENT CHARACTER).
    When raise_on_error is True, decode errors propagate.
    """
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, ValueError):
        if raise_on_error:
            return path.read_text()
        return path.read_text(errors="replace")


async def read_safe_async(path: Path, *, raise_on_error: bool = False) -> str:
    apath = anyio.Path(path)
    try:
        return await apath.read_text(encoding="utf-8")
    except (UnicodeDecodeError, ValueError):
        if raise_on_error:
            return await apath.read_text()
        return await apath.read_text(errors="replace")
