from __future__ import annotations

from collections.abc import Iterator
import locale
import os
from pathlib import Path
from typing import NamedTuple

import anyio
from charset_normalizer import from_bytes


class ReadSafeResult(NamedTuple):
    r"""Text decoded from a file, the codec used, and the detected newline style.

    ``text`` is always normalized to use ``\n`` line endings regardless of the
    original file. ``newline`` records the original style (``"\n"``, ``"\r\n"``,
    or ``"\r"``) so callers can round-trip writes via ``open(..., newline=...)``.
    When no newline is present, defaults to ``os.linesep`` to match Python's
    default text-mode write behavior.
    """

    text: str
    encoding: str
    newline: str = os.linesep


def _detect_newline(text: str) -> str:
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    cr = text.count("\r") - crlf
    counts = {"\r\n": crlf, "\n": lf, "\r": cr}
    best = max(counts, key=lambda nl: counts[nl])
    return best if counts[best] > 0 else os.linesep


def _encodings_from_bom(raw: bytes) -> str | None:
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if raw.startswith(b"\xff\xfe\x00\x00"):
        return "utf-32-le"
    if raw.startswith(b"\x00\x00\xfe\xff"):
        return "utf-32-be"
    if raw.startswith(b"\xff\xfe"):
        return "utf-16-le"
    if raw.startswith(b"\xfe\xff"):
        return "utf-16-be"
    return None


def _encoding_from_best_match(raw: bytes) -> str | None:
    if not (match := from_bytes(raw).best()):
        return None
    return match.encoding


def _get_candidate_encodings(raw: bytes) -> Iterator[str]:
    """Yield candidate encodings lazily — expensive detection runs only if needed."""
    seen: set[str] = set()
    yield "utf-8"
    if (bom := _encodings_from_bom(raw)) and bom not in seen:
        yield bom
    if (
        locale_encoding := locale.getpreferredencoding(False)
    ) and locale_encoding not in seen:
        yield locale_encoding
    if (best := _encoding_from_best_match(raw)) and best not in seen:
        yield best


def decode_safe(raw: bytes, *, raise_on_error: bool = False) -> ReadSafeResult:
    """Decode ``raw`` like :func:`read_safe` after ``read_bytes``.

    Tries UTF-8, locale, BOM, charset-normalizer, then UTF-8 (strict or replace).
    ``UnicodeDecodeError`` can only occur in that last step when
    ``raise_on_error`` is true.
    """
    for encoding in _get_candidate_encodings(raw):
        try:
            text = raw.decode(encoding)
            break
        except (LookupError, UnicodeDecodeError, ValueError):
            pass
    else:
        errors = "strict" if raise_on_error else "replace"
        encoding = "utf-8"
        text = raw.decode(encoding, errors=errors)
    newline = _detect_newline(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return ReadSafeResult(text, encoding, newline)


def read_safe(path: Path, *, raise_on_error: bool = False) -> ReadSafeResult:
    """Read ``path`` and decode with :func:`decode_safe`."""
    return decode_safe(path.read_bytes(), raise_on_error=raise_on_error)


async def read_safe_async(
    path: Path, *, raise_on_error: bool = False
) -> ReadSafeResult:
    """Async :func:`read_safe` (``anyio``)."""
    raw = await anyio.Path(path).read_bytes()
    return decode_safe(raw, raise_on_error=raise_on_error)
