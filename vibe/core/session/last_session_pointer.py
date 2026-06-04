from __future__ import annotations

import os
from pathlib import Path
import re
import sys
from typing import TYPE_CHECKING

from vibe.core.logger import logger
from vibe.core.utils.io import read_safe

if TYPE_CHECKING:
    from vibe.core.config import SessionLoggingConfig


POINTER_DIR_NAME = ".last_session"
_KEY_SANITIZER = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize_key(raw: str) -> str:
    cleaned = _KEY_SANITIZER.sub("_", raw).strip("_")
    return cleaned or "unknown"


def current_tty_key() -> str | None:
    if sys.platform == "win32":
        return _windows_tty_key()
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            fd = stream.fileno()
        except (AttributeError, ValueError, OSError):
            continue
        try:
            tty = os.ttyname(fd)
        except (AttributeError, OSError):
            continue
        return _sanitize_key(Path(tty).name)
    return None


def _windows_tty_key() -> str | None:
    hwnd = _get_console_hwnd()
    if hwnd:
        return _sanitize_key(f"conhost-{hwnd}")
    if wt := os.environ.get("WT_SESSION"):
        return _sanitize_key(f"wt-{wt}")
    return _sanitize_key(f"ppid-{os.getppid()}")


def _get_console_hwnd() -> int:
    import ctypes

    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return 0
    try:
        return int(windll.kernel32.GetConsoleWindow())
    except (AttributeError, OSError):
        return 0


def _pointer_dir(config: SessionLoggingConfig) -> Path:
    return Path(config.save_dir) / POINTER_DIR_NAME


def _pointer_path(config: SessionLoggingConfig, tty_key: str) -> Path:
    return _pointer_dir(config) / tty_key


def record(config: SessionLoggingConfig, session_id: str | None) -> None:
    if not session_id or not config.enabled:
        return
    tty_key = current_tty_key()
    if tty_key is None:
        return
    path = _pointer_path(config, tty_key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{session_id}\n", encoding="utf-8")
    except OSError as e:
        logger.debug("Failed to record last session pointer path=%s err=%s", path, e)


def load(config: SessionLoggingConfig) -> str | None:
    if not config.enabled:
        return None
    tty_key = current_tty_key()
    if tty_key is None:
        return None
    path = _pointer_path(config, tty_key)
    if not path.is_file():
        return None
    try:
        content = read_safe(path).text.strip()
    except OSError as e:
        logger.debug("Failed to read last session pointer path=%s err=%s", path, e)
        return None
    return content or None


def clear_matching(config: SessionLoggingConfig, session_id: str) -> None:
    if not session_id or not config.enabled:
        return

    pointer_dir = _pointer_dir(config)
    if not pointer_dir.is_dir():
        return

    try:
        pointer_paths = list(pointer_dir.iterdir())
    except OSError as e:
        logger.debug(
            "Failed to list last session pointers path=%s err=%s", pointer_dir, e
        )
        return

    for path in pointer_paths:
        if not path.is_file():
            continue
        try:
            content = read_safe(path).text.strip()
        except OSError as e:
            logger.debug("Failed to read last session pointer path=%s err=%s", path, e)
            continue

        if content != session_id:
            continue

        try:
            path.unlink()
        except OSError as e:
            logger.debug("Failed to clear last session pointer path=%s err=%s", path, e)
