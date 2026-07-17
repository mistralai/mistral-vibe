from __future__ import annotations

from vibe.core.checkpoints.fs import DiskFilesystem, Filesystem
from vibe.core.checkpoints.models import FileState


class FileStore:
    """Reads and restores checkpoint file states on disk through a
    :class:`Filesystem` port (fakeable in tests, real disk by default).
    """

    def __init__(self, fs: Filesystem | None = None) -> None:
        self._fs = fs or DiskFilesystem()

    def read(self, path: str) -> FileState:
        return FileState(self._fs.read_bytes(path))

    def apply(self, plan: dict[str, FileState]) -> tuple[list[str], list[str]]:
        """Write a restore/revert plan to disk (delete absent, write present,
        skip no-ops). Returns error messages and the paths actually restored.
        """
        errors: list[str] = []
        restored_paths: list[str] = []
        for path, state in plan.items():
            if state.data is None:
                if not self._fs.exists(path):
                    continue
                try:
                    self._fs.remove(path)
                    restored_paths.append(path)
                except Exception:
                    errors.append(f"Failed to delete file: {path}")
                continue
            if self.read(path) == state:
                continue
            try:
                self._fs.write_bytes(path, state.data)
                restored_paths.append(path)
            except Exception:
                errors.append(f"Failed to restore file: {path}")
        return errors, restored_paths
