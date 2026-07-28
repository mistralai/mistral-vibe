from __future__ import annotations

from pathlib import Path
import shutil
import tempfile

from vibe.observability.logging import logger
from vibe.utils.session_id import shorten_session_id

SCRATCHPAD_PREFIX = "vibe-scratchpad-"


def init_scratchpad(session_id: str) -> Path | None:
    """Create a scratchpad owned by one agent-loop runtime."""
    try:
        dir_path = Path(
            tempfile.mkdtemp(
                prefix=f"{SCRATCHPAD_PREFIX}{shorten_session_id(session_id)}-"
            )
        )
        logger.debug("Scratchpad initialized at %s", dir_path)
        return dir_path
    except OSError:
        logger.warning("Failed to create scratchpad directory")
        return None


def cleanup_scratchpad(scratchpad_dir: Path | None) -> None:
    if scratchpad_dir is None:
        return
    try:
        shutil.rmtree(scratchpad_dir)
    except FileNotFoundError:
        return
    except OSError:
        logger.warning("Failed to clean up scratchpad at %s", scratchpad_dir)


def is_scratchpad_path(path_str: str, *, scratchpad_dir: Path | None) -> bool:
    """Return whether a path is inside this runtime's scratchpad capability."""
    if scratchpad_dir is None:
        return False
    try:
        resolved = Path(path_str).expanduser().resolve()
        return _is_subpath(resolved, scratchpad_dir.resolve())
    except (ValueError, OSError):
        return False


def is_scratchpad_display_path(path_str: str) -> bool:
    try:
        path = Path(path_str).expanduser().resolve()
    except (ValueError, OSError):
        return False
    return any(part.startswith(SCRATCHPAD_PREFIX) for part in path.parts)


def _is_subpath(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False
