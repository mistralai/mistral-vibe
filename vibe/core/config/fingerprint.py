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
        before = _create_file_fingerprint(file)
        yield file, before

    with path.open("rb") as file:
        after = _create_file_fingerprint(file)

    if after != before:
        raise ConcurrencyConflictError(expected_fp=before, actual_fp=after)


def _create_file_fingerprint(file: IO) -> str:
    """Return an opaque token representing the current state of a file."""
    stat = os.fstat(file.fileno())
    return f"{stat.st_dev}:{stat.st_ino}:{stat.st_mtime_ns}:{stat.st_size}"


def create_dict_fingerprint(source: dict[str, Any]) -> str:
    """Return an opaque token representing the current state of a dict."""
    payload = json.dumps(
        to_jsonable_python(source), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode()).hexdigest()
