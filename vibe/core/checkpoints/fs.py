from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol


class Filesystem(Protocol):
    """Disk operations the checkpoint file store needs. ``read_bytes`` returns
    None only when the file is absent, and raises when a file exists but cannot
    be read, so a transient read failure is never mistaken for a deletion. The
    others raise on failure.
    """

    def read_bytes(self, path: str) -> bytes | None: ...

    def write_bytes(self, path: str, data: bytes) -> None: ...

    def remove(self, path: str) -> None: ...

    def exists(self, path: str) -> bool: ...


class DiskFilesystem:
    def read_bytes(self, path: str) -> bytes | None:
        try:
            return Path(path).read_bytes()
        except (FileNotFoundError, NotADirectoryError):
            return None

    def write_bytes(self, path: str, data: bytes) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def remove(self, path: str) -> None:
        os.remove(path)

    def exists(self, path: str) -> bool:
        return Path(path).exists()
