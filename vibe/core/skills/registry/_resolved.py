"""Caches the concrete version a *custom* alias pin currently resolves to.

The reserved ``latest`` alias always means "newest", so it can be resolved from
the store directly. Custom aliases (e.g. ``stable``) may point at an older
version, so we record what each one resolved to on the last refresh, in a
record beside the store, never in the committed manifests. Until it exists,
what an earlier version recorded in ``cache.toml`` stands in for it.

Kept synchronous on purpose: the sync engine offloads the write via
``asyncio.to_thread``, and skill discovery reads it on its sync path.
"""

from __future__ import annotations

from pathlib import Path
import tomllib

from vibe.core.config.harness_files._paths import GLOBAL_REGISTRY_SKILLS_CACHE_DIR
from vibe.core.skills.registry import _records
from vibe.observability.logging import logger
from vibe.utils.vibe_home import get_vibe_home

_LEGACY_SECTION = "registry_resolved_aliases"


def record_path() -> Path:
    return GLOBAL_REGISTRY_SKILLS_CACHE_DIR.path / "resolved-aliases.json"


def _key(skill_id: str, alias: str) -> str:
    return f"{skill_id}@{alias}"


def get(skill_id: str, alias: str) -> int | None:
    """The version ``alias`` last resolved to, or None if never recorded or the
    record cannot be read.
    """
    try:
        return _read().get(_key(skill_id, alias))
    except (OSError, ValueError):
        logger.debug("Could not read the registry alias record", exc_info=True)
        return None


def recorded_versions() -> set[tuple[str, int]]:
    """A registry alias never holds an ``@``. Raises when the record cannot be read, so
    a cleanup stops rather than evict what it keeps.
    """
    return {
        (key.rpartition("@")[0], version)
        for key, version in _read().items()
        if "@" in key
    }


def record(resolved: dict[tuple[str, str], int]) -> None:
    """Persist ``{(skill_id, alias): version}`` mappings (merges with existing).

    A record that cannot be read is left as it is, and a failure is logged.
    """
    if not resolved:
        return
    path = record_path()
    try:
        with _records.writing(path):
            records = _read()
            records.update({
                _key(sid, alias): v for (sid, alias), v in resolved.items()
            })
            _records.replace(path, records)
    except (OSError, ValueError):
        logger.warning(
            "Could not record what registry aliases resolve to", exc_info=True
        )


def _read() -> dict[str, int]:
    """Until the file exists, ``cache.toml`` stands in for it; raises when either cannot
    be read whole.
    """
    try:
        data = _records.read(record_path())
    except FileNotFoundError:
        return _legacy()
    if not isinstance(data, dict):
        raise ValueError(f"{record_path()} holds no alias records")
    return _versions(data)


def _legacy() -> dict[str, int]:
    try:
        with (get_vibe_home() / "cache.toml").open("rb") as file:
            section = tomllib.load(file).get(_LEGACY_SECTION)
    except FileNotFoundError:
        return {}
    return _versions(section) if isinstance(section, dict) else {}


def _versions(records: dict[object, object]) -> dict[str, int]:
    return {
        key: value
        for key, value in records.items()
        if isinstance(key, str)
        and isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    }
