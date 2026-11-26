"""How much disk a folder really uses, read only.

Never follows a symlink, stays on the folder's device, counts a hard link once
per scan and skips what it cannot read. On a macOS volume that reports it, a
file counts its private size: the blocks it shares with no other file, so a
copy-on-write clone of the template counts only what changed since.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
import platform
import time

_BLOCK_BYTES = 512


@dataclass(slots=True)
class Measured:
    bytes: int = 0
    skipped: int = 0
    newest_mtime_ms: int = 0
    # False: the budget ran out before the walk ended.
    complete: bool = True
    # No file counted its allocated size for want of a private one.
    private: bool = True

    def add(self, other: Measured) -> None:
        self.bytes += other.bytes
        self.skipped += other.skipped
        self.newest_mtime_ms = max(self.newest_mtime_ms, other.newest_mtime_ms)
        self.complete = self.complete and other.complete
        self.private = self.private and other.private


@dataclass(slots=True)
class Budget:
    """The scan's time limit, checked between folders.

    No cancel: the walk runs in the background worker process, which the app
    server stops, killing a scan under way, when it shuts down.
    """

    deadline: float = float("inf")
    clock: Callable[[], float] = time.monotonic

    def exhausted(self) -> bool:
        return self.clock() >= self.deadline


class SeenFiles:
    """Files with several hard links already counted in this scan."""

    def __init__(self) -> None:
        self._seen: set[tuple[int, int]] = set()

    def first(self, device: int, inode: int) -> bool:
        key = (device, inode)
        if key in self._seen:
            return False
        self._seen.add(key)
        return True


def measure(
    root: Path, budget: Budget, seen: SeenFiles, *, allocated: bool = False
) -> Measured:
    """``allocated``: the blocks each file points to, shared or not. The
    template counts this way, so the blocks the worktrees cloned from it share
    count once, there.
    """
    if not allocated and _bulk_reader_available():
        from vibe.app_server._storage_usage.sizes_darwin import measure_darwin

        measured = measure_darwin(root, budget, seen)
        if measured is not None:
            return measured
    from vibe.app_server._storage_usage.sizes_portable import measure_portable

    return measure_portable(root, budget, seen)


def measure_excluding(
    root: Path, excluded: frozenset[Path], budget: Budget, seen: SeenFiles
) -> Measured:
    """``root`` without the folders in ``excluded``, measured on their own.

    Stays on ``root``'s device, as ``measure`` does: a folder mounted under
    it is skipped, not counted.
    """
    try:
        device = root.stat(follow_symlinks=False).st_dev
    except FileNotFoundError:
        return Measured()
    except OSError:
        return Measured(skipped=1)
    return _measure_excluding(root, excluded, budget, seen, device)


def _measure_excluding(
    root: Path, excluded: frozenset[Path], budget: Budget, seen: SeenFiles, device: int
) -> Measured:
    if root in excluded:
        return Measured()
    if not any(root in folder.parents for folder in excluded):
        return measure(root, budget, seen)
    result = Measured()
    try:
        with os.scandir(root) as listing:
            entries = list(listing)
    except FileNotFoundError:
        return result
    except OSError:
        result.skipped += 1
        return result
    for entry in entries:
        # A Windows directory junction, followed by is_dir: never walked.
        if entry.is_junction():
            continue
        path = Path(entry.path)
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
            entry_device = _device_of(entry)
        except FileNotFoundError:
            continue
        except OSError:
            result.skipped += 1
            continue
        # 0: unknown (Windows directory entries), never another device.
        if entry_device not in {device, 0}:
            continue
        if is_dir:
            result.add(_measure_excluding(path, excluded, budget, seen, device))
        else:
            result.add(measure_file(path, seen))
    return result


def _device_of(entry: os.DirEntry[str]) -> int:
    return entry.stat(follow_symlinks=False).st_dev


def measure_file(path: Path, seen: SeenFiles) -> Measured:
    result = Measured()
    try:
        stat = path.lstat()
    except FileNotFoundError:
        return result
    except OSError:
        result.skipped += 1
        return result
    result.newest_mtime_ms = int(stat.st_mtime * 1000)
    if stat.st_nlink > 1 and stat.st_ino and not seen.first(stat.st_dev, stat.st_ino):
        return result
    result.private = False
    blocks = getattr(stat, "st_blocks", None)
    result.bytes = stat.st_size if blocks is None else blocks * _BLOCK_BYTES
    return result


def reads_private_sizes() -> bool:
    """Whether this system can report private sizes at all."""
    return _bulk_reader_available()


def _bulk_reader_available() -> bool:
    return platform.system() == "Darwin"


__all__ = [
    "Budget",
    "Measured",
    "SeenFiles",
    "measure",
    "measure_excluding",
    "measure_file",
    "reads_private_sizes",
]
