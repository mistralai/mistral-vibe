from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from vibe.core.config.harness_files._paths import (
    GLOBAL_AGENTS_DIR,
    GLOBAL_PROMPTS_DIR,
    GLOBAL_SKILLS_DIR,
    GLOBAL_TOOLS_DIR,
)
from vibe.core.paths import AGENTS_MD_FILENAME, VIBE_HOME, walk_local_config_dirs_all
from vibe.core.trusted_folders import trusted_folders_manager
from vibe.core.utils.io import read_safe

FileSource = Literal["user", "project"]


@dataclass(frozen=True)
class HarnessFilesManager:
    sources: tuple[FileSource, ...] = ("user",)
    cwd: Path | None = field(default=None)

    @property
    def _effective_cwd(self) -> Path:
        return self.cwd or Path.cwd()

    @property
    def trusted_workdir(self) -> Path | None:
        """Return cwd if project source is enabled and trusted, else None."""
        if "project" not in self.sources:
            return None
        cwd = self._effective_cwd
        if trusted_folders_manager.is_trusted(cwd) is not True:
            return None
        return cwd

    @property
    def config_file(self) -> Path | None:
        workdir = self.trusted_workdir
        if workdir is not None:
            candidate = workdir / ".vibe" / "config.toml"
            if candidate.is_file():
                return candidate
        if "user" in self.sources:
            return VIBE_HOME.path / "config.toml"
        return None

    @property
    def persist_allowed(self) -> bool:
        return "user" in self.sources

    @property
    def user_tools_dirs(self) -> list[Path]:
        if "user" not in self.sources:
            return []
        d = GLOBAL_TOOLS_DIR.path
        return [d] if d.is_dir() else []

    @property
    def user_skills_dirs(self) -> list[Path]:
        if "user" not in self.sources:
            return []
        d = GLOBAL_SKILLS_DIR.path
        return [d] if d.is_dir() else []

    @property
    def user_agents_dirs(self) -> list[Path]:
        if "user" not in self.sources:
            return []
        d = GLOBAL_AGENTS_DIR.path
        return [d] if d.is_dir() else []

    def _walk_project_dirs(
        self,
    ) -> tuple[tuple[Path, ...], tuple[Path, ...], tuple[Path, ...]]:
        workdir = self.trusted_workdir
        if workdir is None:
            return ((), (), ())
        return walk_local_config_dirs_all(workdir)

    @property
    def project_tools_dirs(self) -> list[Path]:
        return list(self._walk_project_dirs()[0])

    @property
    def project_skills_dirs(self) -> list[Path]:
        return list(self._walk_project_dirs()[1])

    @property
    def project_agents_dirs(self) -> list[Path]:
        return list(self._walk_project_dirs()[2])

    @property
    def user_config_file(self) -> Path:
        return VIBE_HOME.path / "config.toml"

    @property
    def project_prompts_dirs(self) -> list[Path]:
        workdir = self.trusted_workdir
        if workdir is None:
            return []
        candidate = workdir / ".vibe" / "prompts"
        return [candidate] if candidate.is_dir() else []

    @property
    def user_prompts_dirs(self) -> list[Path]:
        if "user" not in self.sources:
            return []
        d = GLOBAL_PROMPTS_DIR.path
        return [d] if d.is_dir() else []

    def load_user_doc(self) -> str:
        if "user" not in self.sources:
            return ""
        path = VIBE_HOME.path / AGENTS_MD_FILENAME
        try:
            stripped = read_safe(path).strip()
            return stripped if stripped else ""
        except (FileNotFoundError, OSError):
            return ""

    def _collect_agents_md(
        self, start: Path, stop: Path, *, stop_inclusive: bool
    ) -> list[tuple[Path, str]]:
        """Walk up from start toward stop, collecting non-empty AGENTS.md files.

        Returns ``(directory, content)`` pairs ordered outermost-first.
        When ``stop_inclusive`` is True the stop directory is included in the
        walk; when False the walk stops before reaching it.
        """
        if not start.is_relative_to(stop):
            return []

        docs: list[tuple[Path, str]] = []
        current = start
        while True:
            if current == stop and not stop_inclusive:
                break
            path = current / AGENTS_MD_FILENAME
            try:
                stripped = read_safe(path).strip()
                if stripped:
                    docs.append((current, stripped))
            except (FileNotFoundError, OSError):
                pass
            if current == stop:
                break
            parent = current.parent
            if parent == current:  # fs-root safety
                break
            current = parent
        docs.reverse()  # outermost first
        return docs

    def find_subdirectory_agents_md(self, file_path: Path) -> list[tuple[Path, str]]:
        """Find AGENTS.md files between file_path's parent and cwd (exclusive of cwd).

        For lazy injection when reading files in subdirectories below cwd.
        Returns (directory, content) pairs, outermost first.
        Does not overlap with load_project_docs() which covers cwd and above.
        """
        workdir = self.trusted_workdir
        if workdir is None:
            return []
        cwd = workdir.resolve()
        try:
            resolved = file_path.resolve()
        except (ValueError, OSError):
            return []
        if not resolved.is_relative_to(cwd):
            return []
        start = resolved if resolved.is_dir() else resolved.parent
        return self._collect_agents_md(start, cwd, stop_inclusive=False)

    def load_project_docs(self) -> list[tuple[Path, str]]:
        """Walk up from cwd to the trust root, collecting AGENTS.md files.

        Returns ``(directory, content)`` pairs ordered outermost-first
        (trust root first, cwd last).  Later entries take priority.
        """
        workdir = self.trusted_workdir
        if workdir is None:
            return []
        cwd = workdir.resolve()
        trust_root = trusted_folders_manager.find_trust_root(cwd)
        if trust_root is None:
            return []
        return self._collect_agents_md(cwd, trust_root, stop_inclusive=True)


_manager: HarnessFilesManager | None = None


def init_harness_files_manager(*sources: FileSource) -> None:
    global _manager
    if _manager is not None:
        if _manager.sources == sources:
            return
        raise RuntimeError(
            "HarnessFilesManager already initialized with different sources"
        )
    _manager = HarnessFilesManager(sources=sources)


def get_harness_files_manager() -> HarnessFilesManager:
    if _manager is None:
        raise RuntimeError(
            "HarnessFilesManager not initialized — call init_harness_files_manager() first"
        )
    return _manager


def reset_harness_files_manager() -> None:
    """Reset the singleton. Only intended for use in tests."""
    global _manager
    _manager = None
