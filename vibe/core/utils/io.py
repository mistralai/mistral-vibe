from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
import contextlib
from contextlib import asynccontextmanager
import locale
import os
from pathlib import Path
import shutil
import time
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


def normalize_newlines(text: str) -> tuple[str, str]:
    r"""Return ``text`` with ``\n`` newlines and the detected original style."""
    newline = _detect_newline(text)
    return text.replace("\r\n", "\n").replace("\r", "\n"), newline


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
    text, newline = normalize_newlines(text)
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


class BoundedReadResult(NamedTuple):
    r"""A bounded slice of a file's lines plus truncation metadata.

    ``lines`` are decoded and ``\n``-normalized, without trailing newlines.
    ``total_lines`` is ``None`` when the read stopped early at the line or byte
    budget (the true total is unknown without scanning the rest of the file);
    otherwise it is the number of lines in the file. ``was_truncated`` is true
    when the read stopped before reaching end of file.
    """

    lines: list[str]
    total_lines: int | None
    was_truncated: bool


def read_lines_safe(
    path: Path, *, start_line: int = 1, limit: int, max_bytes: int
) -> BoundedReadResult:
    r"""Read up to ``limit`` lines from ``start_line`` (1-indexed) bounded by bytes.

    Streams the file line-by-line in binary and stops once ``limit`` lines or
    ``max_bytes`` of selected content have been collected, so large files are
    never loaded whole. The collected bytes are decoded once via
    :func:`decode_safe`, which also normalizes ``\r\n``/``\r`` to ``\n``.
    """
    raw_lines: list[bytes] = []
    bytes_read = 0
    line_number = 0
    was_truncated = True

    with path.open("rb") as f:
        while raw_line := f.readline():
            line_number += 1
            if line_number < start_line:
                continue
            if len(raw_lines) >= limit:
                break
            if bytes_read + len(raw_line) > max_bytes:
                remaining = max_bytes - bytes_read
                if remaining > 0:
                    raw_lines.append(raw_line[:remaining])
                break
            raw_lines.append(raw_line)
            bytes_read += len(raw_line)
        else:
            was_truncated = False

    total_lines = None if was_truncated else line_number
    lines = decode_safe(b"".join(raw_lines)).text.splitlines()
    return BoundedReadResult(lines, total_lines, was_truncated)


async def read_lines_safe_async(
    path: Path, *, start_line: int = 1, limit: int, max_bytes: int
) -> BoundedReadResult:
    """Async :func:`read_lines_safe` (runs the blocking read in a thread)."""
    return await asyncio.to_thread(
        read_lines_safe, path, start_line=start_line, limit=limit, max_bytes=max_bytes
    )


_FILE_WRITE_LOCKS: dict[str, asyncio.Lock] = {}
_FILE_WRITE_LOCK_LOOP: asyncio.AbstractEventLoop | None = None


def _get_lock(path: Path) -> asyncio.Lock:
    global _FILE_WRITE_LOCK_LOOP
    loop = asyncio.get_running_loop()
    if _FILE_WRITE_LOCK_LOOP is not loop:
        _FILE_WRITE_LOCKS.clear()
        _FILE_WRITE_LOCK_LOOP = loop
    key = str(path.resolve())
    lock = _FILE_WRITE_LOCKS.get(key)
    if lock is None:
        lock = _FILE_WRITE_LOCKS[key] = asyncio.Lock()
    return lock


@asynccontextmanager
async def file_write_lock(path: Path) -> AsyncIterator[None]:
    async with _get_lock(path):
        yield


async def atomic_replace(
    path: Path, content: str, *, encoding: str = "utf-8", newline: str | None = None
) -> None:
    target = Path(path)
    tmp = target.parent / f".{target.name}.tmp.{os.getpid()}.{time.time_ns()}"
    try:
        async with await anyio.Path(tmp).open(
            mode="w", encoding=encoding, newline=newline
        ) as f:
            await f.write(content)
        with contextlib.suppress(FileNotFoundError):
            shutil.copymode(target, tmp)
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        raise
