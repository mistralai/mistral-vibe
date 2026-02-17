from __future__ import annotations

from pathlib import Path
from typing import Literal

from vibe.core.paths.global_paths import VIBE_HOME, GlobalPath
from vibe.core.trusted_folders import trusted_folders_manager

_config_paths_locked: bool = True


class ConfigPath(GlobalPath):
    @property
    def path(self) -> Path:
        if _config_paths_locked:
            raise RuntimeError("Config path is locked")
        return super().path


def _resolve_config_path(basename: str, type: Literal["file", "dir"]) -> Path:
    cwd = Path.cwd()
    is_folder_trusted = trusted_folders_manager.is_trusted(cwd)
    if not is_folder_trusted:
        return VIBE_HOME.path / basename
    if type == "file":
        if (candidate := cwd / ".vibe" / basename).is_file():
            return candidate
    elif type == "dir":
        if (candidate := cwd / ".vibe" / basename).is_dir():
            return candidate
    return VIBE_HOME.path / basename


def resolve_local_tools_dir(dir: Path) -> Path | None:
    if not trusted_folders_manager.is_trusted(dir):
        return None
    if (candidate := dir / ".vibe" / "tools").is_dir():
        return candidate
    return None


def resolve_local_skills_dirs(dir: Path) -> list[Path]:
    if not trusted_folders_manager.is_trusted(dir):
        return []
    return [
        candidate
        for candidate in [dir / ".vibe" / "skills", dir / ".agents" / "skills"]
        if candidate.is_dir()
    ]


def resolve_local_agents_dir(dir: Path) -> Path | None:
    if not trusted_folders_manager.is_trusted(dir):
        return None
    if (candidate := dir / ".vibe" / "agents").is_dir():
        return candidate
    return None


def unlock_config_paths() -> None:
    global _config_paths_locked
    _config_paths_locked = False


CONFIG_FILE = ConfigPath(lambda: _resolve_config_path("config.toml", "file"))
CONFIG_DIR = ConfigPath(lambda: CONFIG_FILE.path.parent)
PROMPTS_DIR = ConfigPath(lambda: _resolve_config_path("prompts", "dir"))
HISTORY_FILE = ConfigPath(lambda: _resolve_config_path("vibehistory", "file"))
