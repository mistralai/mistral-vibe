from __future__ import annotations

from pathlib import Path
import tomllib

import tomli_w

from vibe.core.paths import (
    AGENTS_MD_FILENAME,
    TRUSTED_FOLDERS_FILE,
    find_local_config_dirs,
)


def has_agents_md_file(path: Path) -> bool:
    return (path / AGENTS_MD_FILENAME).is_file()


def _is_git_repo_root(path: Path) -> bool:
    git_dir = path / ".git"
    return git_dir.is_dir() and (git_dir / "HEAD").is_file()


def find_git_repo_ancestor(path: Path) -> Path | None:
    """Closest ancestor (or *path*) with a real ``.git/HEAD``.

    Excludes the home directory and the filesystem root.
    """
    resolved = path.expanduser().resolve()
    home = Path.home().resolve()
    current = resolved
    while current not in {home, current.parent}:
        if _is_git_repo_root(current):
            return current
        current = current.parent
    return None


def find_trustable_files(path: Path) -> list[str]:
    """Relative paths of files/dirs under *path* that would modify agent behavior."""
    resolved = path.resolve()
    found: list[str] = []

    if has_agents_md_file(path):
        found.append(AGENTS_MD_FILENAME)

    for config_dir in find_local_config_dirs(path).config_dirs:
        label = f"{config_dir.relative_to(resolved)}/"
        if label not in found:
            found.append(label)

    return sorted(found)


def find_repo_trustable_files_for_cwd(cwd: Path, repo_root: Path | None) -> list[str]:
    """Repo-context files that influence *cwd* when inside a git repository.

    Includes:
    - all trustable files at ``repo_root``
    - all ``AGENTS.md`` files on ancestors between ``cwd`` and ``repo_root``
    """
    if repo_root is None:
        return []

    resolved_cwd = cwd.resolve()
    resolved_repo_root = repo_root.resolve()
    if resolved_repo_root not in resolved_cwd.parents:
        return []

    found = set(find_trustable_files(resolved_repo_root))

    current = resolved_cwd.parent
    while current != resolved_repo_root:
        if has_agents_md_file(current):
            relative_path = (current / AGENTS_MD_FILENAME).relative_to(
                resolved_repo_root
            )
            found.add(relative_path.as_posix())
        current = current.parent

    return sorted(found)


class TrustedFoldersManager:
    def __init__(self) -> None:
        self._file_path = TRUSTED_FOLDERS_FILE.path
        self._trusted: list[str] = []
        self._untrusted: list[str] = []
        self._session_trusted: list[str] = []
        self._load()

    def trust_for_session(self, path: Path) -> None:
        self._session_trusted.append(self._normalize_path(path))

    def _normalize_path(self, path: Path) -> str:
        return str(path.expanduser().resolve())

    def _load(self) -> None:
        if not self._file_path.is_file():
            self._trusted = []
            self._untrusted = []
            self._save()
            return

        try:
            with self._file_path.open("rb") as f:
                data = tomllib.load(f)
            self._trusted = list(data.get("trusted", []))
            self._untrusted = list(data.get("untrusted", []))
        except (OSError, tomllib.TOMLDecodeError):
            self._trusted = []
            self._untrusted = []
            self._save()

    def _save(self) -> None:
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"trusted": self._trusted, "untrusted": self._untrusted}
        try:
            with self._file_path.open("wb") as f:
                tomli_w.dump(data, f)
        except OSError:
            pass

    def _closest_decision(self, path: Path) -> tuple[bool, Path] | None:
        """``(trusted, ancestor)`` for the closest decision, ``None`` if undecided."""
        current = Path(self._normalize_path(path))
        while True:
            s = str(current)
            if s in self._trusted or s in self._session_trusted:
                return True, current
            if s in self._untrusted:
                return False, current
            if current.parent == current:
                return None
            current = current.parent

    def is_trusted(self, path: Path) -> bool | None:
        """Tri-state closest decision; ``None`` when no ancestor has one."""
        match self._closest_decision(path):
            case (trusted, _):
                return trusted
            case None:
                return None

    def is_explicitly_untrusted(self, path: Path) -> bool:
        """*path* literally in the untrusted list (no ancestor walk)."""
        return self._normalize_path(path) in self._untrusted

    def find_trust_root(self, path: Path) -> Path | None:
        """Closest explicitly trusted ancestor; ``None`` if a closer untrust blocks."""
        match self._closest_decision(path):
            case (True, root):
                return root
            case _:
                return None

    def add_trusted(self, path: Path) -> None:
        normalized = self._normalize_path(path)
        if normalized not in self._trusted:
            self._trusted.append(normalized)
        if normalized in self._untrusted:
            self._untrusted.remove(normalized)
        self._save()

    def add_untrusted(self, path: Path) -> None:
        normalized = self._normalize_path(path)
        if normalized not in self._untrusted:
            self._untrusted.append(normalized)
        if normalized in self._trusted:
            self._trusted.remove(normalized)
        self._save()


trusted_folders_manager = TrustedFoldersManager()
