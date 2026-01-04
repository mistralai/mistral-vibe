from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path

from vibe import VIBE_ROOT


class GlobalPath:
    def __init__(self, resolver: Callable[[], Path]) -> None:
        self._resolver = resolver

    @property
    def path(self) -> Path:
        return self._resolver()


_DEFAULT_VIBE_HOME = Path.home() / ".vibe"


def _get_vibe_home() -> Path:
    if vibe_home := os.getenv("VIBE_HOME"):
        return Path(vibe_home).expanduser().resolve()
    return _DEFAULT_VIBE_HOME


VIBE_HOME = GlobalPath(_get_vibe_home)
GLOBAL_CONFIG_FILE = GlobalPath(lambda: VIBE_HOME.path / "config.toml")
GLOBAL_ENV_FILE = GlobalPath(lambda: VIBE_HOME.path / ".env")
GLOBAL_TOOLS_DIR = GlobalPath(lambda: VIBE_HOME.path / "tools")
GLOBAL_SKILLS_DIR = GlobalPath(lambda: VIBE_HOME.path / "skills")
SESSION_LOG_DIR = GlobalPath(lambda: VIBE_HOME.path / "logs" / "session")
TRUSTED_FOLDERS_FILE = GlobalPath(lambda: VIBE_HOME.path / "trusted_folders.toml")
LOG_DIR = GlobalPath(lambda: VIBE_HOME.path / "logs")
LOG_FILE = GlobalPath(lambda: VIBE_HOME.path / "vibe.log")

DEFAULT_TOOL_DIR = GlobalPath(lambda: VIBE_ROOT / "core" / "tools" / "builtins")
GLOBAL_CONVERSATIONS_DIR = GlobalPath(lambda: VIBE_HOME.path / "conversations")


def ensure_conversations_dir_exists() -> Path:
    """Ensure the conversations directory exists and return its path."""
    conv_dir = GLOBAL_CONVERSATIONS_DIR.path
    conv_dir.mkdir(parents=True, exist_ok=True)
    return conv_dir
