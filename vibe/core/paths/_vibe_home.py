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


def is_acp_mode() -> bool:
    """Detect if the current process is running in ACP server mode.

    Set by ``vibe/acp/entrypoint.py`` at startup so that
    path-resolution policies can distinguish ACP from CLI contexts.
    """
    return os.getenv("ACP_MODE") == "1"


def resolve_vibe_home(cwd: Path) -> Path:
    """Resolve the VIBE_HOME directory based on execution context.

    Policy (highest priority first):
    1. Explicit ``VIBE_HOME`` environment variable — always wins.
    2. ACP mode (``is_acp_mode() == True``) — use ``.vibe/`` inside *cwd*.
       This keeps all runtime artifacts inside the project boundary so
       that ACP clients (Zed, VS Code, …) can permit the writes.
    3. Default — ``~/.vibe`` for global CLI / standalone usage.
    """
    if vibe_home := os.getenv("VIBE_HOME"):
        return Path(vibe_home).expanduser().resolve()

    if is_acp_mode():
        return (cwd / ".vibe").resolve()

    return _DEFAULT_VIBE_HOME


def _get_vibe_home() -> Path:
    if vibe_home := os.getenv("VIBE_HOME"):
        return Path(vibe_home).expanduser().resolve()
    return _DEFAULT_VIBE_HOME


VIBE_HOME = GlobalPath(_get_vibe_home)
GLOBAL_ENV_FILE = GlobalPath(lambda: VIBE_HOME.path / ".env")
SESSION_LOG_DIR = GlobalPath(lambda: VIBE_HOME.path / "logs" / "session")
TRUSTED_FOLDERS_FILE = GlobalPath(lambda: VIBE_HOME.path / "trusted_folders.toml")
LOG_DIR = GlobalPath(lambda: VIBE_HOME.path / "logs")
LOG_FILE = GlobalPath(lambda: VIBE_HOME.path / "logs" / "vibe.log")
CACHE_FILE = GlobalPath(lambda: VIBE_HOME.path / "cache.toml")
HISTORY_FILE = GlobalPath(lambda: VIBE_HOME.path / "vibehistory")
PLANS_DIR = GlobalPath(lambda: VIBE_HOME.path / "plans")

DEFAULT_TOOL_DIR = GlobalPath(lambda: VIBE_ROOT / "core" / "tools" / "builtins")
