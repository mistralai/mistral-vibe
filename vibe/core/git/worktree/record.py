from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import os
from pathlib import Path
import tempfile

from pydantic import BaseModel, ConfigDict

from vibe.core.paths import WORKTREES_DIR
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
HOLDERS_DIR_NAME = "holders"
_BUCKET_AND_NAME_PARTS = 2


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


def managed_bucket_name(repo_root: Path, common_git_dir: Path) -> str:
    repo_hash = hashlib.sha256(str(common_git_dir).encode()).hexdigest()[:12]
    return f"{repo_root.name}-{repo_hash}"


def _claims_root() -> Path:
    return WORKTREES_DIR.path.resolve() / CLAIMS_DIR_NAME


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
    # made, and neither is the sweep's to touch.
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

    @property
    def directory(self) -> Path:
        return _claims_root() / self.bucket / self.name

    def write(self, record: WorktreeRecord) -> None:
        directory = self.directory
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / RECORD_FILENAME
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
                handle.write(record.model_dump_json(indent=2))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

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

    def delete(self) -> None:
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
        holder = self._holder_path(session_id)
        holder.parent.mkdir(parents=True, exist_ok=True)
        holder.touch()

    def remove_holder(self, session_id: str) -> None:
        self._holder_path(session_id).unlink(missing_ok=True)
        # A delete that ran while this holder was still up could not rmdir past
        # it, and nothing revisits the leftovers - the sweep skips a claim whose
        # record is gone. So the last holder out of a deleted claim takes the
        # empty skeleton with it. Guarded on the record: a live claim keeps its
        # directory even with no one in it.
        if not (self.directory / RECORD_FILENAME).exists():
            self._discard_empty_directories()

    def holders(self) -> frozenset[str]:
        directory = self.directory / HOLDERS_DIR_NAME
        try:
            return frozenset(entry.name for entry in directory.iterdir())
        except OSError:
            return frozenset()
