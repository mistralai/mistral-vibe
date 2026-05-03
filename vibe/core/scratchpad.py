from __future__ import annotations

from pathlib import Path
import tempfile

from vibe.core.logger import logger

_active_scratchpads: dict[str, Path] = {}


def init_scratchpad(session_id: str) -> Path | None:
    """Create a session-scoped scratchpad directory.

    Each session gets its own scratchpad. Idempotent per session_id.
    """
    if session_id in _active_scratchpads:
        return _active_scratchpads[session_id]

    try:
        dir_path = Path(tempfile.mkdtemp(prefix=f"vibe-scratchpad-{session_id[:8]}-"))
        _active_scratchpads[session_id] = dir_path
        logger.debug("Scratchpad initialized at %s", dir_path)
        return dir_path
    except OSError:
        logger.warning("Failed to create scratchpad directory")
        return None


def get_scratchpad_dir(session_id: str) -> Path | None:
    """Return the scratchpad directory for a given session, or None."""
    return _active_scratchpads.get(session_id)


def is_scratchpad_path(path_str: str) -> bool:
    """Return True if the resolved path is inside any active scratchpad.

    Uses Path.resolve() to defeat path traversal and symlink attacks.
    """
    if not _active_scratchpads:
        return False
    try:
        resolved = Path(path_str).expanduser().resolve()
        return any(
            _is_subpath(resolved, sp_dir.resolve())
            for sp_dir in _active_scratchpads.values()
        )
    except (ValueError, OSError):
        return False


def _is_subpath(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False
