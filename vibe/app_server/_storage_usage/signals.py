"""What tells a scan a worktree may have changed since it was measured.

Three file times, each read without running git:

- its sessions' activity: the newest file in their histories, children
  included (the scan walks them anyway);
- git's index, in the admin folder the worktree's ``.git`` file names (git may
  have added digits to the name: ``dashboard1``);
- the last ``pnpm install`` at its root.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_PNPM_STATE = Path("node_modules") / ".modules.yaml"


@dataclass(frozen=True, slots=True)
class Signals:
    histories_mtime: int = 0
    index_mtime: int = 0
    modules_mtime: int = 0

    def to_json(self) -> dict[str, int]:
        return {
            "historiesMtime": self.histories_mtime,
            "indexMtime": self.index_mtime,
            "modulesMtime": self.modules_mtime,
        }

    @classmethod
    def from_json(cls, value: object) -> Signals | None:
        if not isinstance(value, dict):
            return None
        fields = [
            value.get(key) for key in ("historiesMtime", "indexMtime", "modulesMtime")
        ]
        if not all(
            isinstance(item, int) and not isinstance(item, bool) for item in fields
        ):
            return None
        histories, index, modules = (int(item) for item in fields)  # pyright: ignore[reportArgumentType]
        return cls(histories, index, modules)


def git_admin_dir(worktree: Path) -> Path | None:
    """The folder named by the ``gitdir:`` line of the worktree's ``.git`` file."""
    try:
        text = (worktree / ".git").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("gitdir:"):
            target = Path(line.removeprefix("gitdir:").strip())
            return target if target.is_absolute() else (worktree / target)
    return None


def worktree_signals(
    worktree: Path, histories_mtime: int, admin: Path | None
) -> Signals:
    return Signals(
        histories_mtime=histories_mtime,
        index_mtime=_mtime_ms(admin / "index") if admin is not None else 0,
        modules_mtime=_mtime_ms(worktree / _PNPM_STATE),
    )


def is_copy_on_write(admin: Path | None) -> bool:
    """``copy_on_write.populate`` leaves ``core.checkStat = minimal`` in the
    worktree's own config after a successful clone.
    """
    if admin is None:
        return False
    try:
        text = (admin / "config.worktree").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    compact = "".join(text.split()).lower()
    return "checkstat=minimal" in compact


def _mtime_ms(path: Path) -> int:
    try:
        return int(path.stat().st_mtime * 1000)
    except OSError:
        return 0


__all__ = ["Signals", "git_admin_dir", "is_copy_on_write", "worktree_signals"]
