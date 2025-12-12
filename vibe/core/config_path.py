from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path


class ConfigPath:
    def __init__(self, path_resolver: Callable[[], Path]) -> None:
        self._path_resolver = path_resolver

    @property
    def path(self) -> Path:
        return self._path_resolver()


_DEFAULT_VIBE_HOME = Path.home() / ".vibe"


def _get_vibe_home() -> Path:
    if vibe_home := os.getenv("VIBE_HOME"):
        return Path(vibe_home).expanduser().resolve()
    return _DEFAULT_VIBE_HOME


def _resolve_config_file() -> Path:
    if (candidate := Path.cwd() / ".vibe" / "config.toml").is_file():
        return candidate
    return _get_vibe_home() / "config.toml"


def resolve_local_tools_dir(dir: Path) -> Path | None:
    if (candidate := dir / ".vibe" / "tools").is_dir():
        return candidate
    return None


VIBE_HOME = ConfigPath(_get_vibe_home)
GLOBAL_CONFIG_FILE = ConfigPath(lambda: VIBE_HOME.path / "config.toml")
GLOBAL_ENV_FILE = ConfigPath(lambda: VIBE_HOME.path / ".env")
GLOBAL_TOOLS_DIR = ConfigPath(lambda: VIBE_HOME.path / "tools")
SESSION_LOG_DIR = ConfigPath(lambda: VIBE_HOME.path / "logs" / "session")

CONFIG_FILE = ConfigPath(_resolve_config_file)
CONFIG_DIR = ConfigPath(lambda: CONFIG_FILE.path.parent)
LOG_DIR = ConfigPath(lambda: CONFIG_FILE.path.parent / "logs")
AGENT_DIR = ConfigPath(lambda: CONFIG_FILE.path.parent / "agents")
PROMPT_DIR = ConfigPath(lambda: CONFIG_FILE.path.parent / "prompts")
INSTRUCTIONS_FILE = ConfigPath(lambda: CONFIG_FILE.path.parent / "instructions.md")
HISTORY_FILE = ConfigPath(lambda: CONFIG_FILE.path.parent / "vibehistory")
LOG_FILE = ConfigPath(lambda: CONFIG_FILE.path.parent / "vibe.log")
