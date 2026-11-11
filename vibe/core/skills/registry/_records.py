"""Each record is replaced in one rename under its own lock, so a reader never sees half
of one and two writers never drop each other's entries.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, suppress
import json
import os
from pathlib import Path

from vibe.core.skills.registry import _store
from vibe.utils.io import read_safe


def read(path: Path) -> object:
    """Raises :class:`FileNotFoundError` when it is missing, which callers treat as
    empty, and :class:`OSError` or :class:`ValueError` when it cannot be read whole.
    """
    return json.loads(read_safe(path, raise_on_error=True).text)


@contextmanager
def writing(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _store.locked(path.with_name(f"{path.name}.lock")):
        yield


def replace(path: Path, value: object) -> None:
    """The bytes reach the disk before the rename, so a crash never leaves an empty
    record no reader can read again.
    """
    staging = _store.staging_path(path)
    try:
        with staging.open("w", encoding="utf-8") as file:
            file.write(json.dumps(value, sort_keys=True))
            file.flush()
            os.fsync(file.fileno())
        os.replace(staging, path)
    except BaseException:
        with suppress(OSError):
            staging.unlink(missing_ok=True)
        raise
