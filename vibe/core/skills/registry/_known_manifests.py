"""Through this record a repository nobody opened lately keeps the versions it pins by
number.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from vibe.core.config.harness_files._paths import GLOBAL_REGISTRY_SKILLS_CACHE_DIR
from vibe.core.skills.registry import _records
from vibe.observability.logging import logger


def record_path() -> Path:
    return GLOBAL_REGISTRY_SKILLS_CACHE_DIR.path / "project-manifests.json"


def remember(manifests: Iterable[Path]) -> None:
    """A record that cannot be read is never written over: the projects it names would
    lose their pins' protection.
    """
    path = record_path()
    try:
        present = {
            str(manifest.resolve()) for manifest in manifests if manifest.is_file()
        }
        if not present:
            return
        with _records.writing(path):
            known = _read()
            kept = {entry for entry in known if Path(entry).is_file()} | present
            if kept != known:
                _records.replace(path, sorted(kept))
    except (OSError, ValueError):
        logger.warning("Could not record the project manifests read", exc_info=True)


def remembered() -> list[Path]:
    """Raises when the record cannot be read, so a cleanup stops rather than evict what
    a project pins.
    """
    return [Path(entry) for entry in sorted(_read()) if Path(entry).is_file()]


def _read() -> set[str]:
    try:
        entries = _records.read(record_path())
    except FileNotFoundError:
        return set()
    if not isinstance(entries, list):
        raise ValueError(f"{record_path()} lists no project manifests")
    return {entry for entry in entries if isinstance(entry, str)}
