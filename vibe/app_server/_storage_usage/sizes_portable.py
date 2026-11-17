"""Disk usage of a folder with ``os.scandir``: Linux, Windows, and any macOS
volume the bulk reader cannot read.

The allocated size: blocks on POSIX, the file size on Windows, which has no
inode to count a hard link once by.
"""

from __future__ import annotations

import os
from pathlib import Path

from vibe.app_server._storage_usage.sizes import Budget, Measured, SeenFiles

_BLOCK_BYTES = 512


def measure_portable(root: Path, budget: Budget, seen: SeenFiles) -> Measured:
    result = Measured()
    try:
        root_device = root.stat(follow_symlinks=False).st_dev
    except FileNotFoundError:
        return result
    except OSError:
        result.skipped += 1
        return result
    pending = [root]
    while pending:
        if budget.exhausted():
            result.complete = False
            return result
        folder = pending.pop()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    _add_entry(entry, root_device, result, seen, pending)
        except FileNotFoundError:
            continue
        except OSError:
            result.skipped += 1
    return result


def _add_entry(
    entry: os.DirEntry[str],
    root_device: int,
    result: Measured,
    seen: SeenFiles,
    pending: list[Path],
) -> None:
    # A Windows directory junction: is_dir is true and stat reports no device
    # or inode, so neither check below would stop a walk of its target.
    if entry.is_junction():
        return
    try:
        stat = entry.stat(follow_symlinks=False)
        is_dir = entry.is_dir(follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError:
        result.skipped += 1
        return
    # Another device: a mount point, never part of this folder's usage.
    if stat.st_dev not in {root_device, 0}:
        return
    result.newest_mtime_ms = max(result.newest_mtime_ms, int(stat.st_mtime * 1000))
    if is_dir:
        pending.append(Path(entry.path))
        return
    if stat.st_nlink > 1 and stat.st_ino and not seen.first(stat.st_dev, stat.st_ino):
        return
    result.private = False
    blocks = getattr(stat, "st_blocks", None)
    result.bytes += stat.st_size if blocks is None else blocks * _BLOCK_BYTES


__all__ = ["measure_portable"]
