from __future__ import annotations

from pathlib import Path
import tomllib

import tomli_w

from vibe.core.paths.global_paths import TRUSTED_FOLDERS_FILE

TRUSTABLE_FILENAMES = ["AGENTS.md", "VIBE.md", ".vibe.md"]


def has_trustable_content(path: Path) -> bool:
    if (path / ".vibe").exists():
        return True
    for name in TRUSTABLE_FILENAMES:
        if (path / name).exists():
            return True
    return False


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
        normalized = self._normalize_path(path)
        if normalized in self._trusted:
            return True
        if normalized in self._untrusted:
            return False
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
