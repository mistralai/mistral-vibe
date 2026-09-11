from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import hashlib
import os
from pathlib import Path
import tempfile
from threading import Lock
from typing import BinaryIO

from pydantic import BaseModel, ConfigDict

from vibe.core.paths import WORKTREES_DIR
from vibe.core.session.session_lease import (
    _acquire_file_lock,
    _lease_directory_lock,
    _release_file_lock,
)
from vibe.core.utils.time import utc_now
from vibe.observability.logging import logger

# Claims live beside the repo buckets rather than inside them for two reasons.
# A file inside the worktree would show up in `git status --untracked-files=all`
# and permanently fail the cleanup check, and a file inside the bucket
# directory would make `git worktree add` reject the directory the atomic mkdir
# claim just reserved. A bucket is always "<name>-<12 hex>", so this leading-dot
# name cannot collide with one.
CLAIMS_DIR_NAME = ".claims"
RECORD_FILENAME = "record.json"
RECOVERY_FILENAME = "recovery.json"
HOLDERS_DIR_NAME = "holders"
_BUCKET_AND_NAME_PARTS = 2
_STARTING_HOLDER = ".starting"
_PRUNE_LOCK_FILENAME = ".prune"
_HELD_FILES: dict[Path, tuple[BinaryIO, int]] = {}
_HELD_FILES_LOCK = Lock()


class WorktreeRecordError(Exception): ...


class WorktreeRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: int = 1
    name: str
    branch: str
    repo_root: Path
    # None between the mkdir claim and a completed `git worktree add`. Such a
    # record describes a reservation, not yet a worktree.
    base_commit: str | None = None
    branch_created: bool
    claimed_at: datetime

    @classmethod
    def new(
        cls, *, name: str, branch: str, repo_root: Path, branch_created: bool
    ) -> WorktreeRecord:
        return cls(
            name=name,
            branch=branch,
            repo_root=repo_root,
            branch_created=branch_created,
            claimed_at=utc_now(),
        )


class WorktreeRecoveryRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: int = 1
    name: str
    branch: str
    repo_root: Path
    base_commit: str
    snapshot_ref: str
    removed_at: datetime

    @classmethod
    def new(
        cls, record: WorktreeRecord, *, snapshot_ref: str
    ) -> WorktreeRecoveryRecord:
        if record.base_commit is None:
            raise WorktreeRecordError("Cannot recover an incomplete worktree claim.")
        return cls(
            name=record.name,
            branch=record.branch,
            repo_root=record.repo_root,
            base_commit=record.base_commit,
            snapshot_ref=snapshot_ref,
            removed_at=utc_now(),
        )


def managed_bucket_name(repo_root: Path, common_git_dir: Path) -> str:
    repo_hash = hashlib.sha256(str(common_git_dir).encode()).hexdigest()[:12]
    return f"{repo_root.name}-{repo_hash}"


def _claims_root() -> Path:
    return WORKTREES_DIR.path.resolve() / CLAIMS_DIR_NAME


@contextmanager
def worktree_prune_lock() -> Iterator[None]:
    root = _claims_root()
    root.mkdir(parents=True, exist_ok=True)
    with (root / _PRUNE_LOCK_FILENAME).open("a+b") as file:
        _acquire_file_lock(file, blocking=True)
        try:
            yield
        finally:
            _release_file_lock(file)


# Identifies one managed worktree. Bucket and name are a pair that is meaningless
# apart and indistinguishable as bare strings, so they travel as one value: a
# transposed argument would otherwise read and delete a plausible wrong path
# without any type error.
@dataclass(frozen=True, kw_only=True)
class WorktreeClaim:
    bucket: str
    name: str

    @classmethod
    def locate(cls, path: Path) -> WorktreeClaim | None:
        managed_root = WORKTREES_DIR.path.resolve()
        try:
            relative = path.resolve().relative_to(managed_root)
        except (OSError, ValueError):
            return None
        parts = relative.parts
        if len(parts) < _BUCKET_AND_NAME_PARTS or parts[0] == CLAIMS_DIR_NAME:
            return None
        return cls(bucket=parts[0], name=parts[1])

    # Only claimed names, never a listing of the bucket itself: a directory there
    # with no claim is either a live mkdir reservation or something the user
    # made, and neither belongs to automatic cleanup.
    @classmethod
    def in_bucket(cls, bucket: str) -> tuple[WorktreeClaim, ...]:
        # iterdir() is lazy, so the tuple must be built inside the try: a missing
        # bucket raises on first iteration, not on the call.
        try:
            return tuple(
                cls(bucket=bucket, name=entry.name)
                for entry in (_claims_root() / bucket).iterdir()
            )
        except OSError:
            return ()

    @classmethod
    def all(cls) -> tuple[WorktreeClaim, ...]:
        try:
            buckets = tuple(_claims_root().iterdir())
        except OSError:
            return ()
        return tuple(
            claim
            for bucket in buckets
            if bucket.is_dir()
            for claim in cls.in_bucket(bucket.name)
        )

    @property
    def directory(self) -> Path:
        return _claims_root() / self.bucket / self.name

    def write(self, record: WorktreeRecord) -> None:
        self._write_json(RECORD_FILENAME, record)

    def read(self) -> WorktreeRecord | None:
        target = self.directory / RECORD_FILENAME
        try:
            raw = target.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            return WorktreeRecord.model_validate_json(raw)
        except ValueError:
            # Fail closed: an unreadable record means the worktree is not treated
            # as Vibe-owned, so nothing is ever deleted on the strength of one.
            # The file stays put because it may be the only remaining breadcrumb.
            logger.warning("Ignoring unreadable worktree record at %s", target)
            return None

    def has_recovery(self) -> bool:
        return (self.directory / RECOVERY_FILENAME).exists()

    def write_recovery(self, recovery: WorktreeRecoveryRecord) -> None:
        self._write_json(RECOVERY_FILENAME, recovery)

    def read_recovery(self) -> WorktreeRecoveryRecord | None:
        target = self.directory / RECOVERY_FILENAME
        try:
            raw = target.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            return WorktreeRecoveryRecord.model_validate_json(raw)
        except ValueError:
            logger.warning("Ignoring unreadable worktree recovery at %s", target)
            return None

    def delete_recovery(self) -> None:
        (self.directory / RECOVERY_FILENAME).unlink(missing_ok=True)
        self._discard_empty_directories()

    def delete(self) -> None:
        self.finish_starting()
        (self.directory / RECORD_FILENAME).unlink(missing_ok=True)
        self._discard_empty_directories()

    def _discard_empty_directories(self) -> None:
        directory = self.directory
        # rmdir, never rmtree: a surviving holder means a live session, and losing
        # its marker would let the next release delete the worktree underneath it.
        for parent in (directory / HOLDERS_DIR_NAME, directory, directory.parent):
            try:
                parent.rmdir()
            except FileNotFoundError:
                continue
            except OSError:
                return

    # A holder is an empty file named for a session that is currently working in
    # the worktree. Sessions run in separate app-server processes with nothing
    # shared between them, so presence of the marker is the only liveness signal
    # available without a lock. A hard-killed process leaves its marker behind
    # and the worktree is kept forever, which is the safe direction to fail in.
    def _holder_path(self, session_id: str) -> Path:
        holders = self.directory / HOLDERS_DIR_NAME
        # A session id names a file, so anything that could climb out of the
        # holders directory is rejected outright rather than sanitised into
        # something that still unlinks the wrong path.
        candidate = (holders / session_id).resolve()
        if candidate.parent != holders.resolve() or not session_id:
            raise WorktreeRecordError(f"Unusable worktree holder id {session_id!r}.")
        return candidate

    def add_holder(self, session_id: str) -> None:
        self._acquire_holder(self._holder_path(session_id))

    def remove_holder(self, session_id: str) -> None:
        self._release_holder(self._holder_path(session_id))
        # A delete that ran while this holder was still up could not rmdir past
        # it, and nothing revisits the leftovers - pruning skips a claim whose
        # record is gone. So the last holder out of a deleted claim takes the
        # empty skeleton with it. Guarded on the record: a live claim keeps its
        # directory even with no one in it.
        if not (self.directory / RECORD_FILENAME).exists():
            self._discard_empty_directories()

    def holders(self) -> frozenset[str]:
        return self._live_holders(exclude={_STARTING_HOLDER})

    def mark_starting(self) -> None:
        self._acquire_holder(
            self._holder_path(_STARTING_HOLDER), reference_counted=False
        )

    def finish_starting(self) -> None:
        self._release_holder(self._holder_path(_STARTING_HOLDER))
        self._discard_empty_directories()

    def is_starting(self) -> bool:
        return bool(self._live_holders(include={_STARTING_HOLDER}))

    def _acquire_holder(self, holder: Path, *, reference_counted: bool = True) -> None:
        holder.parent.mkdir(parents=True, exist_ok=True)
        with _holder_registry_lock():
            with _HELD_FILES_LOCK:
                if held := _HELD_FILES.get(holder):
                    if not reference_counted:
                        raise WorktreeRecordError(
                            f"Worktree holder {holder.name!r} is already active."
                        )
                    _HELD_FILES[holder] = (held[0], held[1] + 1)
                    return
                file = holder.open("a+b")
                try:
                    _acquire_file_lock(file)
                except BlockingIOError as exc:
                    file.close()
                    raise WorktreeRecordError(
                        f"Worktree holder {holder.name!r} is already active."
                    ) from exc
                _HELD_FILES[holder] = (file, 1)

    def _release_holder(self, holder: Path) -> None:
        if not holder.exists() and not _holder_is_owned(holder):
            return
        with _holder_registry_lock():
            with _HELD_FILES_LOCK:
                held = _HELD_FILES.get(holder)
                if held is not None:
                    file, count = held
                    if count > 1:
                        _HELD_FILES[holder] = (file, count - 1)
                        return
                    del _HELD_FILES[holder]
                    try:
                        _release_file_lock(file)
                    finally:
                        file.close()
                    holder.unlink(missing_ok=True)
                    return
            _discard_stale_holder(holder)

    def _live_holders(
        self, *, include: set[str] | None = None, exclude: set[str] | None = None
    ) -> frozenset[str]:
        directory = self.directory / HOLDERS_DIR_NAME
        live: set[str] = set()
        with _holder_registry_lock():
            try:
                entries = tuple(directory.iterdir())
            except FileNotFoundError:
                return frozenset()
            for entry in entries:
                if include is not None and entry.name not in include:
                    continue
                if exclude is not None and entry.name in exclude:
                    continue
                if _holder_is_live(entry):
                    live.add(entry.name)
        return frozenset(live)

    def _write_json(self, filename: str, model: BaseModel) -> None:
        directory = self.directory
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / filename
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".json.tmp",
                dir=str(directory),
                delete=False,
                encoding="utf-8",
            ) as handle:
                temporary = Path(handle.name)
                handle.write(model.model_dump_json(indent=2))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


@contextmanager
def _holder_registry_lock() -> Iterator[None]:
    root = _claims_root()
    root.mkdir(parents=True, exist_ok=True)
    with _lease_directory_lock(root):
        yield


def _holder_is_owned(holder: Path) -> bool:
    with _HELD_FILES_LOCK:
        return holder in _HELD_FILES


def _holder_is_live(holder: Path) -> bool:
    if _holder_is_owned(holder):
        return True
    return not _discard_stale_holder(holder)


def _discard_stale_holder(holder: Path) -> bool:
    try:
        file = holder.open("a+b")
    except OSError:
        return False
    try:
        _acquire_file_lock(file)
    except BlockingIOError:
        file.close()
        return False
    try:
        _release_file_lock(file)
    finally:
        file.close()
    try:
        holder.unlink(missing_ok=True)
    except OSError:
        return False
    return True
