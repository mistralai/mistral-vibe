from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
from typing import IO, Any

from pydantic_core import to_jsonable_python

from vibe.core.config.types import ConcurrencyConflictError


@contextmanager
def capture_stable_file(path: Path) -> Iterator[tuple[IO[bytes], str]]:
    """Yield a file and fingerprint, raising if the path changes before exit."""
    with path.open("rb") as file:
        before = create_file_fingerprint(file)
        yield file, before

    with path.open("rb") as file:
        after = create_file_fingerprint(file)

    if after != before:
        raise ConcurrencyConflictError(expected_fp=before, actual_fp=after)


_FINGERPRINT_CHUNK_SIZE = 1 << 16


def create_file_fingerprint(file: IO) -> str:
    """Return an opaque token representing the current state of a file.

    The token covers both the file's identity and its contents. Identity alone
    is not sufficient: replacing a file's contents with different data of the
    same length within a single filesystem timestamp tick leaves st_size,
    st_mtime_ns and st_ctime_ns all unchanged (both timestamps come from the
    same clock), so a stat-only token cannot observe the change and a config
    reload silently keeps serving stale values.

    Contents are read with os.pread() so that the caller's file position is
    left untouched -- capture_stable_file() hands the same open file to its
    caller -- and so that the O_RDWR handle from tempfile.NamedTemporaryFile()
    used by the atomic-write path can be fingerprinted as well.
    """
    fd = file.fileno()
    stat = os.fstat(fd)
    digest = hashlib.sha256()
    offset = 0
    while chunk := os.pread(fd, _FINGERPRINT_CHUNK_SIZE, offset):
        digest.update(chunk)
        offset += len(chunk)
    return (
        f"{stat.st_dev}:{stat.st_ino}:{stat.st_mtime_ns}:{stat.st_size}"
        f":{digest.hexdigest()}"
    )


def create_dict_fingerprint(source: dict[str, Any]) -> str:
    """Return an opaque token representing the current state of a dict."""
    payload = json.dumps(
        to_jsonable_python(source), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode()).hexdigest()
