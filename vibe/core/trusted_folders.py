from __future__ import annotations

from pathlib import Path
import tomllib

import tomli_w

from vibe.core.paths import (
    AGENTS_MD_FILENAME,
    TRUSTED_FOLDERS_FILE,
    has_config_dirs_nearby,
)


def has_agents_md_file(path: Path) -> bool:
    return (path / AGENTS_MD_FILENAME).exists()


def has_trustable_content(path: Path) -> bool:
    if (path / ".vibe").exists():
        return True
    if has_agents_md_file(path):
        return True
    return has_config_dirs_nearby(path)


class TrustedFoldersManager:
    def __init__(self) -> None:
        self._file_path = TRUSTED_FOLDERS_FILE.path
        self._trusted: list[str] = []
        self._untrusted: list[str] = []
        self._load()

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

    def is_trusted(self, path: Path) -> bool | None:
        """Check trust walking up from *path* to filesystem root.

        The first ancestor (or *path* itself) found in either the trusted
        or untrusted list wins.  Returns ``None`` when no decision exists.
        """
        current = Path(self._normalize_path(path))
        while True:
            s = str(current)
            if s in self._trusted:
                return True
            if s in self._untrusted:
                return False
            parent = current.parent
            if parent == current:
                break
            current = parent
        return None

    def find_trust_root(self, path: Path) -> Path | None:
        """Return the closest ancestor (or *path* itself) explicitly in the trusted list."""
        current = Path(self._normalize_path(path))
        while True:
            s = str(current)
            if s in self._trusted:
                return current
            parent = current.parent
            if parent == current:
                break
            current = parent
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
