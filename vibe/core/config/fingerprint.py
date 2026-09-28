from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
from typing import IO, Any

from pydantic_core import to_jsonable_python


@contextmanager
def open_fingerprinted_file(path: Path) -> Iterator[tuple[IO[bytes], str]]:
    """Yield a file open for reading and the fingerprint of what it holds.

    The fingerprint comes from ``fstat`` on this very descriptor, so it names
    the bytes the caller is about to read and nothing else. A writer replacing
    the path mid-read cannot invalidate that pairing: a save lands as an atomic
    rename, so the open descriptor keeps pointing at the version it opened.

    Whether the *path* has since moved on is a separate question, and not one
    the reader has to answer -- a patch built on this fingerprint is checked
    against the freshly loaded one at write time, which is where a stale read
    is caught.
    """
    with path.open("rb") as file:
        yield file, create_file_fingerprint(file)


def create_file_fingerprint(file: IO) -> str:
    """Return an opaque token representing the current state of a file."""
    stat = os.fstat(file.fileno())
    return f"{stat.st_dev}:{stat.st_ino}:{stat.st_mtime_ns}:{stat.st_size}"


def create_dict_fingerprint(source: dict[str, Any]) -> str:
    """Return an opaque token representing the current state of a dict."""
    payload = json.dumps(
        to_jsonable_python(source), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode()).hexdigest()
