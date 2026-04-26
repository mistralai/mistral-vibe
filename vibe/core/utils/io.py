from __future__ import annotations

from collections.abc import Iterator
import locale
from pathlib import Path
from typing import NamedTuple

import anyio
from charset_normalizer import from_bytes


class ReadSafeResult(NamedTuple):
    """Text decoded from a file and the codec name that successfully decoded it."""

    text: str
    encoding: str


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
            return ReadSafeResult(raw.decode(encoding), encoding)
        except (LookupError, UnicodeDecodeError, ValueError):
            pass
    errors = "strict" if raise_on_error else "replace"
    return ReadSafeResult(raw.decode("utf-8", errors=errors), "utf-8")


def read_safe(path: Path, *, raise_on_error: bool = False) -> ReadSafeResult:
    """Read ``path`` and decode with :func:`decode_safe`."""
    return decode_safe(path.read_bytes(), raise_on_error=raise_on_error)


async def read_safe_async(
    path: Path, *, raise_on_error: bool = False
) -> ReadSafeResult:
    """Async :func:`read_safe` (``anyio``)."""
    raw = await anyio.Path(path).read_bytes()
    return decode_safe(raw, raise_on_error=raise_on_error)
