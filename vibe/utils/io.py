from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
import contextlib
from contextlib import asynccontextmanager
from functools import lru_cache
import locale
import os
from pathlib import Path
import shutil
import sys
import time
from typing import NamedTuple

import anyio


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
    # Endianless codecs (utf-16/utf-32) auto-detect byte order from the BOM and
    # strip it, symmetric with utf-8-sig; the explicit -le/-be variants leave a
    # stray U+FEFF in the decoded text. Check the 32-bit BOMs first: the
    # utf-32-le BOM has the utf-16-le BOM as a prefix.
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if raw.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return "utf-32"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    return None


def _encoding_from_best_match(raw: bytes) -> str | None:
    from charset_normalizer import from_bytes

    if not (match := from_bytes(raw).best()):
        return None
    return match.encoding


@lru_cache(maxsize=1)
def _windows_oem_encoding() -> str | None:
    # Windows console output is OEM (cp850), not ANSI (cp1252 from locale).
    # Only correct for console output (see ``decode_console_safe``); file reads
    # must not use it.
    if sys.platform != "win32":
        return None
    import ctypes

    return f"cp{ctypes.windll.kernel32.GetOEMCP()}"


def _get_candidate_encodings(
    raw: bytes, preferred_encoding: str | None = None
) -> Iterator[str]:
    """Yield candidate encodings lazily — expensive detection runs only if needed."""
    seen: set[str] = set()

    def _emit(encoding: str | None) -> Iterator[str]:
        if encoding and (key := encoding.lower()) not in seen:
            seen.add(key)
            yield encoding

    yield from _emit(_encodings_from_bom(raw))
    yield from _emit("utf-8")
    yield from _emit(preferred_encoding)
    yield from _emit(locale.getpreferredencoding(False))
    yield from _emit(_encoding_from_best_match(raw))


def normalize_newlines(text: str) -> tuple[str, str]:
    r"""Return ``text`` with ``\n`` newlines and the detected original style."""
    if "\r" not in text:
        newline = "\n" if "\n" in text else os.linesep
        return text, newline
    newline = _detect_newline(text)
    return text.replace("\r\n", "\n").replace("\r", "\n"), newline


def normalize_edit_strings(old: str, new: str) -> tuple[str, str]:
    r"""Return an edit's old and new text with ``\n`` line endings.

    A trailing lone ``\r`` is the first half of a CRLF copied from a
    ``read_file`` line. On ``old`` it becomes ``\n``, so the text still only
    matches at the end of a line, and ``new`` then ends with ``\n`` too, so the
    replacement keeps that line break. On ``new`` alone it is dropped, unless
    ``old`` ends a line.
    """
    old_ends_with_cr = old.endswith("\r")
    old = normalize_newlines(old)[0]
    if not old.endswith("\n"):
        new = new.removesuffix("\r")
    new = normalize_newlines(new)[0]
    if old_ends_with_cr and not new.endswith("\n"):
        new += "\n"
    return old, new


def _decode(
    raw: bytes, *, raise_on_error: bool, preferred: str | None
) -> tuple[str, str]:
    for encoding in _get_candidate_encodings(raw, preferred):
        try:
            return raw.decode(encoding), encoding
        except (LookupError, UnicodeDecodeError, ValueError):
            pass
    errors = "strict" if raise_on_error else "replace"
    return raw.decode("utf-8", errors=errors), "utf-8"


def decode_safe(raw: bytes, *, raise_on_error: bool = False) -> ReadSafeResult:
    """Decode file ``raw`` like :func:`read_safe` after ``read_bytes``.

    Tries BOM, UTF-8, locale, charset-normalizer, then UTF-8 (strict or
    replace). ``UnicodeDecodeError`` can only occur in that last step when
    ``raise_on_error`` is true.
    """
    text, encoding = _decode(raw, raise_on_error=raise_on_error, preferred=None)
    text, newline = normalize_newlines(text)
    return ReadSafeResult(text, encoding, newline)


def decode_console_safe(raw: bytes, *, raise_on_error: bool = False) -> str:
    r"""Decode console output with the same fallback chain as :func:`decode_safe`.

    Prefers the Windows OEM code page over the ANSI locale. Newlines are left
    verbatim: a lone ``\r`` redraws the line rather than ending one, and folding
    ``\r\n`` here would break a reader that pages the output in byte windows,
    because a window can end between the two bytes. Collapsing carriage returns
    is a rendering decision, so it belongs to whoever draws the text.
    """
    text, _encoding = _decode(
        raw, raise_on_error=raise_on_error, preferred=_windows_oem_encoding()
    )
    return text


def encode_safe(text: str, *, encoding: str = "utf-8", newline: str = "\n") -> bytes:
    r"""Inverse of :func:`decode_safe`: translate ``\n`` line endings to
    ``newline`` and encode with ``encoding``, falling back to UTF-8 when the codec
    cannot represent the text. The in-memory sibling of ``atomic_replace``'s
    ``open(..., newline=...)`` write.
    """
    body = text if newline == "\n" else text.replace("\n", newline)
    try:
        return body.encode(encoding)
    except (LookupError, UnicodeError):
        return body.encode("utf-8")


def read_safe(path: Path, *, raise_on_error: bool = False) -> ReadSafeResult:
    """Read ``path`` and decode with :func:`decode_safe`."""
    return decode_safe(path.read_bytes(), raise_on_error=raise_on_error)


async def read_safe_async(
    path: Path, *, raise_on_error: bool = False
) -> ReadSafeResult:
    """Async :func:`read_safe` (``anyio``)."""
    raw = await anyio.Path(path).read_bytes()
    return decode_safe(raw, raise_on_error=raise_on_error)


def split_lines_verbatim(text: str) -> list[str]:
    r"""Split ``text`` on ``\n`` only, keeping any ``\r`` inside its line.

    A CRLF line keeps its trailing ``\r``, so the caller sees the file's line
    endings as shell commands show them.
    """
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


class BoundedReadResult(NamedTuple):
    r"""A bounded slice of a file's lines plus truncation metadata.

    ``lines`` are decoded, without trailing newlines. They are
    ``\n``-normalized unless the read kept carriage returns.
    ``total_lines`` is ``None`` when the read stopped early at the line or byte
    budget (the true total is unknown without scanning the rest of the file);
    otherwise it is the number of lines in the file. ``was_truncated`` is true
    when the read stopped before reaching end of file.
    """

    lines: list[str]
    total_lines: int | None
    was_truncated: bool


def read_lines_safe(
    path: Path,
    *,
    start_line: int = 1,
    limit: int,
    max_bytes: int,
    keep_carriage_returns: bool = False,
) -> BoundedReadResult:
    r"""Read up to ``limit`` lines from ``start_line`` (1-indexed) bounded by bytes.

    Streams the file line-by-line in binary and stops once ``limit`` lines or
    ``max_bytes`` of selected content have been collected, so large files are
    never loaded whole. The collected bytes are decoded once with the
    :func:`decode_safe` fallback chain, and ``\r\n``/``\r`` are normalized to
    ``\n`` unless ``keep_carriage_returns`` is set, in which case they are
    split by :func:`split_lines_verbatim`.
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
    raw = b"".join(raw_lines)
    if keep_carriage_returns:
        text, _encoding = _decode(raw, raise_on_error=False, preferred=None)
        lines = split_lines_verbatim(text)
    else:
        lines = decode_safe(raw).text.splitlines()
    return BoundedReadResult(lines, total_lines, was_truncated)


async def read_lines_safe_async(
    path: Path,
    *,
    start_line: int = 1,
    limit: int,
    max_bytes: int,
    keep_carriage_returns: bool = False,
) -> BoundedReadResult:
    """Async :func:`read_lines_safe` (runs the blocking read in a thread)."""
    return await asyncio.to_thread(
        read_lines_safe,
        path,
        start_line=start_line,
        limit=limit,
        max_bytes=max_bytes,
        keep_carriage_returns=keep_carriage_returns,
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
