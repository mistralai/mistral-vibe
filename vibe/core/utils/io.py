from __future__ import annotations

from pathlib import Path

import anyio
from charset_normalizer import from_bytes


def detect_encoding(raw: bytes) -> str:
    """Detect the encoding of raw bytes using charset_normalizer.

    Returns the best-guess encoding name, defaulting to utf-8 when detection
    is inconclusive.
    """
    if not raw:
        return "utf-8"
    result = from_bytes(raw).best()
    if result is None:
        return "utf-8"
    return result.encoding


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


def read_with_detected_encoding(path: Path) -> tuple[str, str]:
    """Read a text file, detecting its encoding automatically.

    Returns a (content, encoding) tuple. Tries UTF-8 first, then uses
    charset_normalizer to detect the actual encoding for non-UTF-8 files
    (e.g., Shift-JIS, EUC-KR, Windows-1252).
    """
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8"), "utf-8"
    except (UnicodeDecodeError, ValueError):
        encoding = detect_encoding(raw)
        return raw.decode(encoding), encoding


async def read_with_detected_encoding_async(path: Path) -> tuple[str, str]:
    """Async version of read_with_detected_encoding."""
    apath = anyio.Path(path)
    raw = await apath.read_bytes()
    try:
        return raw.decode("utf-8"), "utf-8"
    except (UnicodeDecodeError, ValueError):
        encoding = detect_encoding(raw)
        return raw.decode(encoding), encoding


async def read_safe_async(path: Path, *, raise_on_error: bool = False) -> str:
    apath = anyio.Path(path)
    try:
        return await apath.read_text(encoding="utf-8")
    except (UnicodeDecodeError, ValueError):
        if raise_on_error:
            return await apath.read_text()
        return await apath.read_text(errors="replace")
