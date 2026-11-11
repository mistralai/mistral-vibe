"""The rules are ADR 0021's. Under a version's lock the lease is checked before the
marker, since a session marks a version used before its lease lets go of it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Set
from datetime import timedelta
from pathlib import Path
import time

from vibe.core.config.harness_files._paths import GLOBAL_REGISTRY_SKILLS_CACHE_DIR
from vibe.core.skills.registry import _store
from vibe.core.skills.registry._leases import clear_dead_leases, held_versions
from vibe.core.skills.registry._store import Version
from vibe.observability.logging import logger

UNUSED_FOR = timedelta(days=30)

CLEANUP_EVERY = timedelta(days=1)


def last_cleanup() -> Path:
    return GLOBAL_REGISTRY_SKILLS_CACHE_DIR.path / "last-cleanup"


def claim_cleanup() -> bool:
    """Checked and recorded under one lock taken without waiting, so of two sessions
    that start together only one runs it; a claim that cannot be checked or recorded
    runs none.
    """
    marker = last_cleanup()
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        with _store.locked(marker.with_name(f"{marker.name}.lock"), wait=timedelta(0)):
            if _ran_lately(marker):
                return False
            marker.touch()
    except OSError:
        logger.debug("Skipped the registry skills store cleanup", exc_info=True)
        return False
    return True


def _ran_lately(marker: Path) -> bool:
    try:
        last = marker.stat().st_mtime
    except FileNotFoundError:
        return False
    return last > time.time() - CLEANUP_EVERY.total_seconds()


def unused_versions(pinned: Set[Version]) -> dict[str, list[int]]:
    """A skill that cannot be read is skipped, and logged."""
    root = _store.store_root()
    if not root.is_dir():
        return {}
    _store.sweep(root.parent)
    clear_dead_leases()
    cutoff = _cutoff()
    needed = set(pinned) | held_versions()
    unused: dict[str, list[int]] = {}
    for skill in sorted(root.iterdir()):
        if not skill.is_dir() or not _store.is_safe_skill_id(skill.name):
            continue
        try:
            versions = _unused_of(skill, needed, cutoff)
        except OSError:
            logger.warning(
                "Skipped registry skill %s in the store cleanup",
                skill.name,
                exc_info=True,
            )
            continue
        if versions:
            unused[skill.name] = versions
    return unused


def _unused_of(skill: Path, needed: Set[Version], cutoff: float) -> list[int]:
    """What an earlier eviction or write left behind goes on the way."""
    _store.sweep(skill)
    return [
        version
        for version in _store.stored_versions(skill.name)[:-1]
        if (skill.name, version) not in needed
        and _unused_since(skill.name, version, cutoff)
    ]


def evict_versions(unused: Mapping[str, Iterable[int]]) -> list[Version]:
    cutoff = _cutoff()
    evicted = [
        (skill_id, version)
        for skill_id, versions in unused.items()
        for version in versions
        if _evict(skill_id, version, cutoff)
    ]
    if evicted:
        logger.info("Evicted %d unused registry skill version(s)", len(evicted))
    return evicted


def _evict(skill_id: str, version: int, cutoff: float) -> bool:
    """One whose lock is held is skipped without waiting, and the delete runs once the
    lock is free, so the cleanup never holds up a writer.
    """
    try:
        with _store.version_lock(
            _store.skill_dir(skill_id, version), wait=timedelta(0)
        ):
            trash = _move_aside(skill_id, version, cutoff)
    except OSError:
        logger.debug("Skipped evicting %s v%d", skill_id, version, exc_info=True)
        return False
    if trash is None:
        return False
    _store.delete(trash)
    return True


def _move_aside(skill_id: str, version: int, cutoff: float) -> Path | None:
    """A session marks a version before it checks the version is stored, without the
    lock, so one marking it as it goes gets it back: that session finds it in place, or
    found it gone and resolves again.
    """
    if not _still_unused(skill_id, version, cutoff):
        return None
    dest = _store.skill_dir(skill_id, version)
    trash = _store.move_aside(dest)
    if not _unused_since(skill_id, version, cutoff):
        trash.replace(dest)
        return None
    _store.unmark(skill_id, version)
    return trash


def _still_unused(skill_id: str, version: int, cutoff: float) -> bool:
    """The lease is checked before the marker: a session marks what it lets go of before
    its lease stops saying so. The lease of a session that died since the cleanup began
    holds the version until the next one.
    """
    return (
        _store.is_materialized_sync(skill_id, version)
        and (skill_id, version) not in held_versions()
        and _unused_since(skill_id, version, cutoff)
    )


def _unused_since(skill_id: str, version: int, cutoff: float) -> bool:
    """A version without a marker is marked now, and counts as used."""
    try:
        last_used = _store.used_marker(skill_id, version).stat().st_mtime
    except FileNotFoundError:
        _store.mark_used(skill_id, version)
        return False
    except OSError:
        return False
    return last_used < cutoff


def _cutoff() -> float:
    return time.time() - UNUSED_FOR.total_seconds()
