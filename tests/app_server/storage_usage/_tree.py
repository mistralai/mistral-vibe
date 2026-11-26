"""A fake ``~/.vibe`` for the storage usage tests: sessions, children, claims
and worktrees laid out as Vibe writes them, without running git or a session.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import time

from vibe.core.git.worktree.record import WorktreeClaim, WorktreeRecord

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
NOW_MS = int(NOW.timestamp() * 1000)


@dataclass(frozen=True, slots=True)
class Tree:
    home: Path

    @property
    def sessions_root(self) -> Path:
        return self.home / "logs" / "session"

    @property
    def worktrees_root(self) -> Path:
        return self.home / "worktrees"

    def session(
        self,
        session_id: str,
        *,
        size: int = 1000,
        idle_days: float = 1,
        archived: bool = False,
        title: str | None = None,
        cwd: Path | None = None,
        parent: str | None = None,
        meta: bool = True,
    ) -> Path:
        folder = self.sessions_root / "unified" / session_id
        write(folder / "journal" / "000001", size)
        if meta:
            bumped = (NOW - timedelta(days=idle_days)).isoformat()
            (folder / "meta.json").write_text(
                json.dumps({
                    "session_id": session_id,
                    "parent_session_id": parent,
                    "start_time": bumped,
                    "end_time": bumped,
                    "bumped_at": bumped,
                    "git_commit": None,
                    "git_branch": None,
                    "username": "tester",
                    "archived_at": NOW.isoformat() if archived else None,
                    "title": title,
                    "environment": {
                        "working_directory": None if cwd is None else str(cwd)
                    },
                })
            )
        return folder

    def child(self, child_id: str, root_id: str, *, size: int = 500) -> Path:
        folder = self.sessions_root / "unified" / child_id
        write(folder / "journal" / "000001", size)
        generation = folder / "generations" / "0000000000000002"
        generation.mkdir(parents=True)
        (generation / "runtime-state.json").write_text(
            json.dumps({
                "identity": {"session_id": child_id, "root_session_id": root_id}
            })
        )
        # The newest generation may lack a runtime state: an older one names it.
        (folder / "generations" / "0000000000000003").mkdir()
        return folder

    def worktree(
        self,
        name: str,
        *,
        sessions: list[str] = (),  # type: ignore[assignment]
        size: int = 4000,
        bucket: str = "repo-0123456789ab",
        claim: bool = True,
        admin_name: str | None = None,
        copy_on_write: bool = False,
        claimed_at: datetime = NOW,
    ) -> Path:
        folder = self.worktrees_root / bucket / name
        write(folder / "src" / "main.py", size)
        admin = self.home / "repo" / ".git" / "worktrees" / (admin_name or name)
        admin.mkdir(parents=True, exist_ok=True)
        (admin / "index").write_bytes(b"index")
        if copy_on_write:
            (admin / "config.worktree").write_text(
                "[core]\n\tcheckStat = minimal\n\ttrustctime = false\n"
            )
        (folder / ".git").write_text(f"gitdir: {admin}\n")
        if claim:
            WorktreeClaim(bucket=bucket, name=name).write(
                WorktreeRecord(
                    name=name,
                    branch=f"vibe/{name}",
                    repo_root=self.home / "repo",
                    base_commit="0" * 40,
                    branch_created=True,
                    claimed_at=claimed_at,
                    session_dirs={
                        self.sessions_root / "unified" / item for item in sessions
                    },
                )
            )
        return folder

    def write_history(self, session_id: str) -> None:
        """A turn writing to the session's history: moves its activity signal."""
        folder = self.sessions_root / "unified" / session_id / "journal"
        newest = max((item.stat().st_mtime for item in folder.iterdir()), default=0.0)
        path = folder / "000099"
        write(path, 10)
        os.utime(path, (newest + 5, newest + 5))

    def index_of(self, name: str) -> Path:
        return self.home / "repo" / ".git" / "worktrees" / name / "index"


def write(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(os.urandom(size))


def age(root: Path, seconds: float) -> None:
    """Every folder and file under ``root``, and ``root``, last changed
    ``seconds`` ago.
    """
    when = time.time() - seconds
    for folder, dirs, names in os.walk(root, topdown=False):
        for name in [*dirs, *names]:
            os.utime(os.path.join(folder, name), (when, when), follow_symlinks=False)
    os.utime(root, (when, when))


def bump_mtime(path: Path, seconds: float = 5) -> None:
    stat = path.stat()
    os.utime(path, (stat.st_atime + seconds, stat.st_mtime + seconds))


__all__ = ["NOW", "NOW_MS", "Tree", "age", "bump_mtime", "write"]
