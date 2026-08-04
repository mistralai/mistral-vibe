from __future__ import annotations


class FakeFilesystem:
    """In-memory implementation of the checkpoints ``Filesystem`` port. ``files``
    is the backing store; ``fail_reads``/``fail_writes``/``fail_removes`` name
    paths whose read, write or remove should raise, to exercise error handling.
    """

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files: dict[str, bytes] = dict(files or {})
        self.fail_reads: set[str] = set()
        self.fail_writes: set[str] = set()
        self.fail_removes: set[str] = set()

    def read_bytes(self, path: str) -> bytes | None:
        if path in self.fail_reads:
            raise OSError(f"read failed: {path}")
        return self.files.get(path)

    def write_bytes(self, path: str, data: bytes) -> None:
        if path in self.fail_writes:
            raise OSError(f"write failed: {path}")
        self.files[path] = data

    def remove(self, path: str) -> None:
        if path in self.fail_removes:
            raise OSError(f"remove failed: {path}")
        del self.files[path]

    def exists(self, path: str) -> bool:
        return path in self.files
