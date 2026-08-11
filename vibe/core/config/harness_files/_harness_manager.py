from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from vibe.core.config.harness_files._paths import (
    GLOBAL_AGENTS_DIR,
    GLOBAL_AGENTS_SKILLS_DIR,
    GLOBAL_PROMPTS_DIR,
    GLOBAL_SKILLS_DIR,
    GLOBAL_TOOLS_DIR,
)
from vibe.core.paths import (
    AGENTS_MD_FILENAME,
    VIBE_HOME,
    LocalConfigDirs,
    dedup_paths,
    find_local_config_dirs,
)
from vibe.core.trusted_folders import TrustedFoldersManager, trusted_folders_manager
from vibe.utils.io import read_safe

FileSource = Literal["user", "project"]


@dataclass(frozen=True)
class HarnessFilesManager:
    sources: tuple[FileSource, ...] = ("user",)
    cwd: Path | None = field(default=None)
    _additional_dirs: tuple[Path, ...] = ()
    trust_store: TrustedFoldersManager = field(
        default_factory=lambda: trusted_folders_manager, compare=False, repr=False
    )

    def for_session(
        self, cwd: Path, *, workspace_roots: list[Path] | None = None
    ) -> HarnessFilesManager:
        return replace(
            self,
            cwd=cwd,
            _additional_dirs=tuple(
                dedup_paths([*self._additional_dirs, *(workspace_roots or [])])
            ),
        )

    @property
    def _effective_cwd(self) -> Path:
        return self.cwd or Path.cwd()

    @property
    def workspace_roots(self) -> list[Path]:
        cwd = self._effective_cwd.resolve()
        return [
            cwd,
            *(root for root in dedup_paths(self._additional_dirs) if root != cwd),
        ]

    @property
    def cwd_is_user_config_home(self) -> bool:
        """True when the cwd's ``.vibe`` dir is the user config dir itself."""
        return (self._effective_cwd / ".vibe").resolve() == VIBE_HOME.path.resolve()

    @property
    def project_source_enabled(self) -> bool:
        """True when the project layer is eligible for this cwd."""
        return "project" in self.sources and not self.cwd_is_user_config_home

    @property
    def _trusted_workdir(self) -> Path | None:
        """Return cwd if project source is enabled and trusted, else None."""
        if not self.project_source_enabled:
            return None
        cwd = self._effective_cwd
        if self.trust_store.is_trusted(cwd) is not True:
            return None
        return cwd

    @property
    def config_file(self) -> Path | None:
        workdir = self._trusted_workdir
        if workdir is not None:
            candidate = workdir / ".vibe" / "config.toml"
            if candidate.is_file():
                return candidate
        if "user" in self.sources:
            return VIBE_HOME.path / "config.toml"
        return None

    @property
    def project_roots(self) -> list[Path]:
        """Open project directories: trusted cwd (if any) plus ``--add-dir``
        paths.

        ``--add-dir`` entries are resolved and deduplicated; nested paths are
        preserved because project config discovery is root-level only.
        Add-dirs equal to the cwd are dropped (redundant). When an add-dir
        contains the cwd, both survive (the add-dir contributes its own
        root-level discovery; cwd preserves walk-up semantics for AGENTS.md).
        """
        add_dirs = dedup_paths(self._additional_dirs)
        workdir = self._trusted_workdir
        if workdir is None:
            return add_dirs
        w = workdir.resolve()
        return [w, *(p for p in add_dirs if p != w)]

    @property
    def hook_files(self) -> list[Path]:
        files: list[Path] = [
            root / ".vibe" / "hooks.toml" for root in self.project_roots
        ]
        if "user" in self.sources:
            files.append(VIBE_HOME.path / "hooks.toml")
        return files

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
        return [
            d
            for d in (GLOBAL_SKILLS_DIR.path, GLOBAL_AGENTS_SKILLS_DIR.path)
            if d.is_dir()
        ]

    @property
    def user_agents_dirs(self) -> list[Path]:
        if "user" not in self.sources:
            return []
        d = GLOBAL_AGENTS_DIR.path
        return [d] if d.is_dir() else []

    def _collect_project_roots(self) -> LocalConfigDirs:
        result = LocalConfigDirs()
        for root in self.project_roots:
            result |= find_local_config_dirs(root)
        return result

    @property
    def project_tools_dirs(self) -> list[Path]:
        return list(self._collect_project_roots().tools)

    @property
    def project_skills_dirs(self) -> list[Path]:
        return list(self._collect_project_roots().skills)

    @property
    def project_agents_dirs(self) -> list[Path]:
        return list(self._collect_project_roots().agents)

    @property
    def user_config_file(self) -> Path:
        return VIBE_HOME.path / "config.toml"

    @property
    def project_prompts_dirs(self) -> list[Path]:
        return [
            candidate
            for root in self.project_roots
            if (candidate := root / ".vibe" / "prompts").is_dir()
        ]

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
            stripped = read_safe(path).text.strip()
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
                stripped = read_safe(path).text.strip()
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
        """Find AGENTS.md files between file_path's parent and its containing
        open dir (exclusive of the open dir itself).

        For lazy injection when reading files below any open project root.
        Does not overlap with load_project_docs() which covers the open dir
        and above.
        """
        try:
            resolved = file_path.resolve()
        except (ValueError, OSError):
            return []
        for root in self.project_roots:
            if resolved.is_relative_to(root):
                start = resolved if resolved.is_dir() else resolved.parent
                return self._collect_agents_md(start, root, stop_inclusive=False)
        return []

    def load_project_docs(self) -> list[tuple[Path, str]]:
        """Collect AGENTS.md files from each open project root up to its trust
        root.

        For the trusted cwd entry the trust root is found via
        ``trusted_folders_manager`` (and may sit above cwd). ``--add-dir``
        entries that aren't registered there fall back to the root itself.

        Returns ``(directory, content)`` pairs ordered outermost-first; later
        entries take priority. The same resolved directory is only emitted
        once across all roots.
        """
        by_dir: dict[Path, tuple[Path, str]] = {}
        for root in self.project_roots:
            stop = self.trust_store.find_trust_root(root) or root
            for d, content in self._collect_agents_md(root, stop, stop_inclusive=True):
                by_dir.setdefault(d.resolve(), (d, content))
        return list(by_dir.values())


_manager: HarnessFilesManager | None = None


def init_harness_files_manager(
    *sources: FileSource, additional_dirs: list[Path] | None = None
) -> None:
    """Initialize the global HarnessFilesManager singleton.

    *additional_dirs* are extra working directories supplied via ``--add-dir``.
    They are implicitly trusted (the user opted in via the CLI flag, same
    semantics as ``--trust``) and do not require a trust-folder check.
    """
    global _manager
    candidate = HarnessFilesManager(
        sources=sources, _additional_dirs=tuple(dedup_paths(additional_dirs or []))
    )
    if _manager is not None:
        if _manager == candidate:
            return
        raise RuntimeError(
            "HarnessFilesManager already initialized with different configuration"
        )
    _manager = candidate


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
