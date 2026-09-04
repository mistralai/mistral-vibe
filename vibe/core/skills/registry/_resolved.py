"""Caches the concrete version a *custom* alias pin currently resolves to.

The reserved ``latest`` alias always means "newest", so it can be resolved from
the store directly. Custom aliases (e.g. ``stable``) may point at an older
version, so we record what each one resolved to on the last refresh. State lives
in the shared local cache (``cache.toml``), never in the committed manifests.

Kept synchronous on purpose: the sync engine offloads the write via
``asyncio.to_thread``, and skill discovery reads it on its sync path.
"""

from __future__ import annotations

from vibe.utils.cache_store import FileSystemCacheStore

_SECTION = "registry_resolved_aliases"


def _key(skill_id: str, alias: str) -> str:
    return f"{skill_id}@{alias}"


def get(skill_id: str, alias: str) -> int | None:
    """The version ``alias`` last resolved to, or None if never recorded."""
    value = FileSystemCacheStore().read_section(_SECTION).get(_key(skill_id, alias))
    return value if isinstance(value, int) else None


def record(resolved: dict[tuple[str, str], int]) -> None:
    """Persist ``{(skill_id, alias): version}`` mappings (merges with existing)."""
    if not resolved:
        return
    FileSystemCacheStore().write_section(
        _SECTION, {_key(sid, alias): v for (sid, alias), v in resolved.items()}
    )
