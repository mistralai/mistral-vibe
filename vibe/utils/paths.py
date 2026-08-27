from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
import re

from vibe.utils.platform import is_windows

# fullmatch + DOTALL: a "$" anchor would match before a trailing newline and drop it.
_MSYS_DRIVE = re.compile(r"/([A-Za-z])([/\\].*)?", re.DOTALL)


class UnanchoredWindowsPathError(ValueError):
    """A Windows path that names a drive or a root, but not both."""

    def __init__(self, raw: str) -> None:
        self.raw = raw
        super().__init__(
            f"'{raw}' is not anchored: a Windows path must give both a drive and "
            "a root ('C:\\Users\\...') or neither ('src/main.py'). Windows would "
            f"resolve '{raw}' against the current directory of whichever drive is "
            "active and silently use the wrong location. Use a drive-qualified "
            "path such as 'C:\\Users\\...', a Git Bash path such as "
            "'/c/Users/...', or a path relative to the working directory."
        )


def normalize_windows_path(raw: str) -> str:
    """Fold a Git Bash / MSYS drive path (``/c/Users``) into ``C:/Users``."""
    raw = raw.strip()
    if not is_windows() or not (match := _MSYS_DRIVE.fullmatch(raw)):
        return raw

    suffix = (match[2] or "/").replace("\\", "/")
    return f"{match[1].upper()}:{suffix}"


def target_pure_path(raw: str) -> PurePath:
    """Read a path by the target platform's rules; ``Path`` follows the host's."""
    return PureWindowsPath(raw) if is_windows() else PurePosixPath(raw)


def normalize_windows_input_path(raw: str) -> str:
    """Normalize a model-supplied path, rejecting one Windows cannot anchor."""
    normalized = normalize_windows_path(raw)
    if not is_windows():
        return normalized

    path = PureWindowsPath(normalized)
    if bool(path.root) != bool(path.drive):
        raise UnanchoredWindowsPathError(raw)

    return normalized


class GlobalPath:
    def __init__(self, resolver: Callable[[], Path]) -> None:
        self._resolver = resolver

    @property
    def path(self) -> Path:
        return self._resolver()


_DEFAULT_VIBE_HOME = Path.home() / ".vibe"


def get_vibe_home() -> Path:
    if vibe_home := os.getenv("VIBE_HOME"):
        return Path(vibe_home).expanduser().resolve()
    return _DEFAULT_VIBE_HOME


def is_dangerous_directory(path: Path | str = ".") -> tuple[bool, str]:
    """Check if the current directory is a dangerous folder that would cause
    issues if we were to run the tool there.

    Args:
        path: Path to check (defaults to current directory)

    Returns:
        tuple[bool, str]: (is_dangerous, reason) where reason explains why it's dangerous
    """
    path = Path(path).resolve()

    home_dir = Path.home()

    dangerous_paths = {
        home_dir: "home directory",
        home_dir / "Documents": "Documents folder",
        home_dir / "Desktop": "Desktop folder",
        home_dir / "Downloads": "Downloads folder",
        home_dir / "Pictures": "Pictures folder",
        home_dir / "Movies": "Movies folder",
        home_dir / "Music": "Music folder",
        home_dir / "Library": "Library folder",
        Path("/Applications"): "Applications folder",
        Path("/System"): "System folder",
        Path("/Library"): "System Library folder",
        Path("/usr"): "System usr folder",
        Path("/private"): "System private folder",
    }

    for dangerous_path, description in dangerous_paths.items():
        try:
            if path == dangerous_path:
                return True, f"You are in the {description}"
        except (OSError, ValueError):
            continue
    return False, ""
