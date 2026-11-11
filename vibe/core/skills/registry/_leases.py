"""A lease is a lock the session holds while it lives, and a record beside it, since
Windows cannot read a locked file. A cleanup that can take the lock knows the session is
gone. The record is best effort: a held version is marked used when the lease starts and
stops holding it either way.
"""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import suppress
import os
from pathlib import Path
from typing import BinaryIO
import uuid

from vibe.core.config.harness_files._paths import GLOBAL_REGISTRY_SKILLS_CACHE_DIR
from vibe.core.skills.registry import _records, _store
from vibe.core.skills.registry._store import Version
from vibe.core.utils import file_lock
from vibe.observability.logging import logger


def leases_root() -> Path:
    return GLOBAL_REGISTRY_SKILLS_CACHE_DIR.path / "leases"


class StoreLease:
    def __init__(self) -> None:
        self._name = f"{os.getpid()}-{uuid.uuid4().hex}"
        self._lock: BinaryIO | None = None
        self._held: frozenset[Version] = frozenset()
        self._recorded: frozenset[Version] = frozenset()

    def hold(self, versions: Iterable[Version]) -> None:
        """A version is marked used when the lease starts or stops holding it, before
        the record says so. Raises :class:`OSError` when the lease's lock cannot be
        taken.
        """
        held = frozenset(versions)
        _store.mark_versions_used(held ^ self._held)
        self._held = held
        if held == self._recorded:
            return
        self._take_lock()
        if _write_versions(self._path(".json"), held):
            self._recorded = held

    def release(self) -> None:
        """Marks what it held used first, and never raises: a failure is logged, and a
        later cleanup clears what it left.
        """
        _store.mark_versions_used(self._held)
        self._held = frozenset()
        lock, self._lock = self._lock, None
        self._recorded = frozenset()
        if lock is None:
            return
        try:
            _unlock(lock)
        except Exception:
            logger.warning("Could not release the registry skills lease", exc_info=True)
        for suffix in (".json", ".lock"):
            with suppress(OSError):
                self._path(suffix).unlink(missing_ok=True)

    def _take_lock(self) -> None:
        if self._lock is not None:
            return
        path = self._path(".lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = path.open("a+b")
        try:
            file_lock.acquire_file_lock(lock)
        except BaseException:
            lock.close()
            raise
        self._lock = lock

    def _path(self, suffix: str) -> Path:
        return leases_root() / f"{self._name}{suffix}"


def clear_dead_leases() -> None:
    """A crashed session never let go of its versions, so they are marked used before
    its lease goes.
    """
    root = leases_root()
    if not root.is_dir():
        return
    for record in root.glob("*.json"):
        lock = record.with_suffix(".lock")
        if _owner_alive(lock):
            continue
        _store.mark_versions_used(_read_versions(record))
        for stale in (record, lock):
            with suppress(OSError):
                stale.unlink(missing_ok=True)
    _clear_leftovers(root)


def held_versions() -> set[Version]:
    """A dead session's lease no cleanup cleared yet still holds; reading writes
    nothing.
    """
    root = leases_root()
    if not root.is_dir():
        return set()
    return {
        version for record in root.glob("*.json") for version in _read_versions(record)
    }


def _clear_leftovers(root: Path) -> None:
    _store.sweep(root)
    for path in root.iterdir():
        if (
            path.suffix == ".lock"
            and _store.older_than(path, _store.LEFTOVER_AFTER)
            and not path.with_suffix(".json").exists()
            and not _owner_alive(path)
        ):
            with suppress(OSError):
                path.unlink(missing_ok=True)


def _owner_alive(lock: Path) -> bool:
    """Only a lock file that is gone, or one this process can take, proves the session
    is gone: any other error opening it counts as alive.
    """
    try:
        file = lock.open("r+b")
    except FileNotFoundError:
        return False
    except OSError:
        return True
    with file:
        try:
            file_lock.acquire_file_lock(file)
        except BlockingIOError:
            return True
        file_lock.release_file_lock(file)
    return False


def _unlock(lock: BinaryIO) -> None:
    try:
        file_lock.release_file_lock(lock)
    finally:
        lock.close()


def _read_versions(record: Path) -> set[Version]:
    """One that cannot be read holds nothing, and an entry that is not a safe skill id
    with a version number is skipped.
    """
    try:
        entries = _records.read(record)
    except (OSError, ValueError):
        return set()
    if not isinstance(entries, list):
        return set()
    return {(entry[0], entry[1]) for entry in entries if _is_version(entry)}


def _is_version(entry: object) -> bool:
    match entry:
        case [str() as skill_id, int() as version] if not isinstance(version, bool):
            return _store.is_safe_skill_id(skill_id) and version >= 0
        case _:
            return False


def _write_versions(record: Path, versions: frozenset[Version]) -> bool:
    try:
        _records.replace(record, sorted(versions))
    except OSError as exc:
        logger.warning(
            "Could not record the registry skills this session holds: %s", exc
        )
        return False
    return True
