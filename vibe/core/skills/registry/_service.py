from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum, auto
from http import HTTPStatus
from pathlib import Path
import shutil
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from vibe.core.config import resolve_api_key
from vibe.core.config._defaults import DEFAULT_MISTRAL_API_ENV_KEY
from vibe.core.config.harness_files import get_harness_files_manager
from vibe.core.config.harness_files._paths import GLOBAL_SKILLS_DIR
from vibe.core.skills.models import REGISTRY_LATEST_ALIAS, SkillScope
from vibe.core.skills.registry import _ledger, _manifest, _notify, _resolved, _store
from vibe.core.skills.registry._client import RegistrySkillsClient, RegistrySkillsError
from vibe.core.skills.registry._manifest import ManifestEntry, SkillManifest
from vibe.core.skills.registry.models import SkillVersionInfo
from vibe.observability.logging import logger

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema

_PAGE_SIZE = 100
_MATERIALIZE_CONCURRENCY = 8


class RegistrySyncStatus(StrEnum):
    SKIPPED = auto()
    FAILED = auto()
    OK = auto()


@dataclass(frozen=True)
class RegistrySyncResult:
    status: RegistrySyncStatus
    written: int = 0
    skipped: int = 0


class CatalogItem(BaseModel):
    name: str
    skill_id: str
    description: str
    latest_version: int
    sharing_scope: str = ""


class SkillDetails(BaseModel):
    """Registry object metadata + body for one version, for the details card."""

    name: str
    skill_id: str
    version: int
    body: str
    description: str = ""
    created_by: str = ""
    created_at: str = ""
    last_modified_at: str = ""
    sharing_scope: str = ""
    latest_version: int = 0
    version_created_at: str = ""
    aliases: list[str] = Field(default_factory=list)
    notes: str = ""


@dataclass(frozen=True)
class SkillUpdate:
    name: str
    current_version: int
    latest_version: int


@dataclass(frozen=True)
class _Endpoint:
    api_base: str
    api_key: str


def _resolve_endpoint(config: VibeConfigSchema) -> _Endpoint | None:
    provider = config.get_mistral_provider()
    if provider is None:
        logger.debug("Registry skills enabled but no Mistral provider is configured")
        return None
    env_var = provider.api_key_env_var or DEFAULT_MISTRAL_API_ENV_KEY
    api_key = resolve_api_key(env_var)
    if not api_key:
        logger.info("Registry skills enabled but %s is not set; skipping", env_var)
        return None
    return _Endpoint(api_base=provider.api_base, api_key=api_key)


async def _manifest_paths(roots: Sequence[Path] | None = None) -> list[Path]:
    return [
        _manifest.global_manifest_path(),
        *await _manifest.project_manifest_paths(roots),
    ]


def _unsafe_pin(entry: ManifestEntry) -> bool:
    """Skip (with a log) a committed pin whose id can't be a safe cache path,
    rather than letting the ValueError from the store crash startup.
    """
    if _store.is_safe_skill_id(entry.skill_id):
        return False
    logger.warning(
        "Skipping registry pin '%s': unsafe skill id %r", entry.name, entry.skill_id
    )
    return True


async def refresh_registry_skills(
    config: VibeConfigSchema, roots: Sequence[Path] | None = None
) -> RegistrySyncResult:
    """Session-start sync.

    Resolves every pinned skill to a concrete version and downloads any missing
    bodies into the local store, then prunes versions no longer pinned. Runs so
    that, e.g., a teammate who clones a repo with a committed ``.vibe/skills.toml``
    but no local cache gets the bodies.

    ``roots`` are the caller's session project roots (the agent loop's
    ``harness_files.project_roots``); when omitted the global harness singleton
    is used.
    """
    if not config.experimental_enable_registry_skills:
        return RegistrySyncResult(RegistrySyncStatus.SKIPPED)
    endpoint = await asyncio.to_thread(_resolve_endpoint, config)
    if endpoint is None:
        return RegistrySyncResult(RegistrySyncStatus.SKIPPED)
    if not await _needs_sync(roots):
        await _prune_shared(_ledger.repo_key(roots), await _local_active(roots), roots)
        return RegistrySyncResult(RegistrySyncStatus.SKIPPED)

    try:
        async with RegistrySkillsClient(endpoint.api_base, endpoint.api_key) as client:
            latest_by_id = await _resolve_latest_versions(
                client, await _latest_pinned_skill_ids(roots)
            )
            active, written, skipped = await _sync_pins(client, latest_by_id, roots)
    except RegistrySkillsError as exc:
        logger.warning("Failed to sync registry skills: %s", exc.reason)
        return RegistrySyncResult(RegistrySyncStatus.FAILED)
    except OSError as exc:
        logger.warning("Failed to materialize registry skills: %s", exc)
        return RegistrySyncResult(RegistrySyncStatus.FAILED)

    await _prune_shared(_ledger.repo_key(roots), active, roots)
    logger.info("Synced registry skills: %d fetched, %d skipped", written, skipped)
    return RegistrySyncResult(RegistrySyncStatus.OK, written=written, skipped=skipped)


async def _prune_shared(
    key: str, active: set[tuple[str, int]], roots: Sequence[Path] | None = None
) -> None:
    """Record this repo's active set, then prune against the union across repos.

    The store is shared across repositories, so pruning against only this repo's
    pins would evict a sibling repo's version of the same skill. Recording per
    repo and pruning against the union keeps every repo's pinned versions.

    Each scope records what it actually pins: deriving the repo entry by
    subtracting the global one would drop a project's claim on a version the
    global manifest happens to pin too.
    """
    safe = await _prune_safe(active)
    shared = await _prune_safe(await _scoped_active([_manifest.global_manifest_path()]))
    await asyncio.to_thread(_ledger.record, _ledger.GLOBAL_KEY, shared)
    if key != _ledger.GLOBAL_KEY:
        project = await _prune_safe(
            await _scoped_active(await _manifest.project_manifest_paths(roots))
        )
        await asyncio.to_thread(_ledger.record, key, project)
    keep = await asyncio.to_thread(_ledger.union)
    await _store.prune(keep | safe, recheck=_ledger.union)


async def _scoped_active(paths: Sequence[Path]) -> set[tuple[str, int]]:
    """Active pins recorded by the manifests in ``paths`` only.

    Each ledger scope records what it pins itself, so dropping a pin in one
    scope takes effect on the next sync instead of lingering in another
    scope's entry.
    """
    active: set[tuple[str, int]] = set()
    for path in paths:
        manifest = await _manifest.load(path)
        for entry in manifest.skills:
            if _unsafe_pin(entry):
                continue
            version = await _pinned_version(entry)
            if version is not None:
                active.add((entry.skill_id, version))
    return active


async def _pinned_version(entry: ManifestEntry) -> int | None:
    """The concrete version an entry currently pins, or None if unresolved."""
    if isinstance(entry.version, int):
        return entry.version
    if entry.version == REGISTRY_LATEST_ALIAS:
        return await _store.latest_materialized(entry.skill_id)
    version = await asyncio.to_thread(_resolved.get, entry.skill_id, entry.version)
    if version is None:
        version = await _store.latest_materialized(entry.skill_id)
    return version


async def _needs_sync(roots: Sequence[Path] | None = None) -> bool:
    """Do we need to hit the registry at all?

    True when any alias pin exists (must resolve server-side) or any frozen pin
    is not yet materialized locally.
    """
    manifests = await asyncio.gather(
        *(_manifest.load(path) for path in await _manifest_paths(roots))
    )
    for manifest in manifests:
        for entry in manifest.skills:
            if _unsafe_pin(entry):
                continue
            if entry.alias is not None:
                return True
            if isinstance(entry.version, int) and not await _store.is_materialized(
                entry.skill_id, entry.version
            ):
                return True
    return False


async def _latest_pinned_skill_ids(roots: Sequence[Path] | None = None) -> set[str]:
    """Skill ids pinned to the reserved ``latest`` alias across all manifests."""
    sids: set[str] = set()
    for path in await _manifest_paths(roots):
        for entry in (await _manifest.load(path)).skills:
            if not _unsafe_pin(entry) and entry.version == REGISTRY_LATEST_ALIAS:
                sids.add(entry.skill_id)
    return sids


async def _resolve_latest_versions(
    client: RegistrySkillsClient, skill_ids: set[str]
) -> dict[str, int]:
    """Newest version for each given skill id, fetched one skill at a time."""
    latest: dict[str, int] = {}
    for sid in skill_ids:
        try:
            item = await client.get_skill(sid)
        except RegistrySkillsError as exc:
            if exc.status == HTTPStatus.NOT_FOUND:
                continue
            raise
        latest[sid] = (
            item.metadata.latest_version
            if item.metadata.latest_version > 0
            else item.version
        )
    return latest


def _add_target(
    active: set[tuple[str, int]],
    known: list[tuple[str, int, str]],
    seen: set[tuple[str, int]],
    sid: str,
    version: int,
    name: str,
) -> None:
    """Mark (sid, version) active and queue it for batch materialization once."""
    active.add((sid, version))
    if (sid, version) not in seen:
        seen.add((sid, version))
        known.append((sid, version, name))


async def _sync_alias(
    client: RegistrySkillsClient,
    entry: ManifestEntry,
    alias: str,
    active: set[tuple[str, int]],
    resolved_aliases: dict[tuple[str, str], int],
    alias_seen: set[tuple[str, str]],
) -> tuple[int, int]:
    """Resolve + materialize a custom alias pin; return (written, skipped).

    Resolved inline because ``get_skill(alias=...)`` already returns the body,
    and deduplicated so the same alias in several manifests hits the server once.
    """
    sid = entry.skill_id
    key = (sid, alias)
    if key in alias_seen:
        return 0, 0
    alias_seen.add(key)
    try:
        item = await client.get_skill(sid, alias=alias)
    except RegistrySkillsError as exc:
        logger.warning(
            "Failed to resolve alias '%s' for '%s': %s", alias, entry.name, exc.reason
        )
        fallback = await asyncio.to_thread(_resolved.get, sid, alias)
        if fallback is None:
            fallback = await _store.latest_materialized(sid)
        if fallback is not None:
            active.add((sid, fallback))
        return 0, 0
    resolved_aliases[key] = item.version
    if await _store.is_materialized(sid, item.version):
        active.add((sid, item.version))
        return 0, 0
    if await _store.materialize(item, entry.name) is None:
        fallback = await asyncio.to_thread(_resolved.get, sid, alias)
        if fallback is None:
            fallback = await _store.latest_materialized(sid)
        if fallback is not None:
            logger.debug(
                "Alias '%s' for %s resolved to v%d with an empty body; keeping v%d",
                alias,
                sid,
                item.version,
                fallback,
            )
            active.add((sid, fallback))
        return 0, 1
    active.add((sid, item.version))
    return 1, 0


async def _sync_pins(
    client: RegistrySkillsClient,
    latest_by_id: dict[str, int],
    roots: Sequence[Path] | None = None,
) -> tuple[set[tuple[str, int]], int, int]:
    """Resolve and materialize every pin; return (active versions, written, skipped).

    Frozen ints use their number; ``latest`` uses the catalog's newest version
    (falling back to newest-on-disk if absent); custom aliases resolve
    server-side (cached in ``_resolved`` so discovery finds them offline).
    """
    active: set[tuple[str, int]] = set()
    known: list[tuple[str, int, str]] = []
    seen: set[tuple[str, int]] = set()
    resolved_aliases: dict[tuple[str, str], int] = {}
    alias_seen: set[tuple[str, str]] = set()
    written = skipped = 0

    for path in await _manifest_paths(roots):
        manifest = await _manifest.load(path)
        for entry in manifest.skills:
            if _unsafe_pin(entry):
                continue
            sid = entry.skill_id
            if isinstance(entry.version, int):
                _add_target(active, known, seen, sid, entry.version, entry.name)
                continue
            if entry.version != REGISTRY_LATEST_ALIAS:
                w, s = await _sync_alias(
                    client, entry, entry.version, active, resolved_aliases, alias_seen
                )
                written += w
                skipped += s
                continue
            version = latest_by_id.get(sid)
            if version is None:
                version = await _store.latest_materialized(sid)
                if version is not None:
                    logger.debug(
                        "Skill %s missing from catalog; using on-disk v%d", sid, version
                    )
            if version is None:
                logger.debug(
                    "Skipping '%s': 'latest' has no version to resolve", entry.name
                )
                continue
            _add_target(active, known, seen, sid, version, entry.name)

    await asyncio.to_thread(_resolved.record, resolved_aliases)
    w, s = await _materialize_missing(client, known)
    return await _prune_safe(active), written + w, skipped + s


async def _local_active(roots: Sequence[Path] | None = None) -> set[tuple[str, int]]:
    """Active (skill_id, version) set from local state only (no network).

    Lets the fast path still prune versions no longer pinned when nothing needs
    downloading.
    """
    return await _scoped_active(await _manifest_paths(roots))


async def _prune_safe(active: set[tuple[str, int]]) -> set[tuple[str, int]]:
    """Resolve the active set to versions actually on disk.

    Never prune a skill's only usable body because a new target failed to
    download: for any resolved target that isn't materialized, substitute an
    existing on-disk version so prune doesn't strip the skill entirely.
    """
    safe: set[tuple[str, int]] = set()
    for sid, version in active:
        if await _store.is_materialized(sid, version):
            safe.add((sid, version))
        elif (fallback := await _store.latest_materialized(sid)) is not None:
            safe.add((sid, fallback))
    return safe


async def _materialize_missing(
    client: RegistrySkillsClient, targets: list[tuple[str, int, str]]
) -> tuple[int, int]:
    pending: list[tuple[str, int, str]] = []
    for target in targets:
        if not await _store.is_materialized(target[0], target[1]):
            pending.append(target)
    if not pending:
        return 0, 0
    semaphore = asyncio.Semaphore(_MATERIALIZE_CONCURRENCY)

    async def fetch_one(skill_id: str, version: int, name: str) -> bool:
        """True if the version was written, False if fetch failed or body empty."""
        async with semaphore:
            try:
                item = await client.get_skill(skill_id, version=version)
            except RegistrySkillsError as exc:
                logger.warning("Failed to fetch skill '%s': %s", name, exc.reason)
                return False
        return await _store.materialize(item, name) is not None

    results = await asyncio.gather(*(fetch_one(*target) for target in pending))
    written = sum(results)
    return written, len(results) - written


async def has_registry_endpoint(config: VibeConfigSchema) -> bool:
    """Is a Mistral provider configured with a usable API key?"""
    return await asyncio.to_thread(_resolve_endpoint, config) is not None


async def list_catalog(config: VibeConfigSchema) -> list[CatalogItem]:
    endpoint = await asyncio.to_thread(_resolve_endpoint, config)
    if endpoint is None:
        return []
    async with RegistrySkillsClient(endpoint.api_base, endpoint.api_key) as client:
        items = await client.list_catalog(page_size=_PAGE_SIZE)
    catalog: list[CatalogItem] = []
    for item in items:
        name = item.resolved_name
        if name is None:
            continue
        catalog.append(
            CatalogItem(
                name=name,
                skill_id=item.skill_id,
                description=item.resolved_description,
                latest_version=item.metadata.latest_version or item.version,
                sharing_scope=item.metadata.sharing_scope,
            )
        )
    return catalog


async def get_skill_body(
    config: VibeConfigSchema, skill_id: str, *, version: int | None = None
) -> str:
    """Fetch a single skill's SKILL.md body (for preview/details). No caching."""
    endpoint = await asyncio.to_thread(_resolve_endpoint, config)
    if endpoint is None:
        raise RegistrySkillsError("no authenticated Mistral endpoint available")
    async with RegistrySkillsClient(endpoint.api_base, endpoint.api_key) as client:
        item = await client.get_skill(skill_id, version=version)
    return item.skill.skill_body


async def get_skill_details(
    config: VibeConfigSchema, skill_id: str, *, version: int | None = None
) -> SkillDetails | None:
    """Fetch a skill version's full registry object (metadata + body)."""
    endpoint = await asyncio.to_thread(_resolve_endpoint, config)
    if endpoint is None:
        return None
    try:
        async with RegistrySkillsClient(endpoint.api_base, endpoint.api_key) as client:
            item = await client.get_skill(skill_id, version=version)
    except RegistrySkillsError as exc:
        logger.warning("Failed to load skill details: %s", exc.reason)
        return None
    meta = item.metadata
    return SkillDetails(
        name=item.resolved_name or skill_id,
        skill_id=skill_id,
        version=item.version,
        body=item.skill.skill_body,
        description=item.resolved_description,
        created_by=meta.created_by,
        created_at=meta.created_at,
        last_modified_at=meta.last_modified_at,
        sharing_scope=meta.sharing_scope,
        latest_version=meta.latest_version or item.version,
        version_created_at=item.version_metadata.created_at,
        aliases=item.version_attributes.aliases,
        notes=item.version_attributes.notes,
    )


async def import_skill(
    config: VibeConfigSchema,
    skill_id: str,
    *,
    version: int | None = None,
    alias: str | None = None,
    scope: SkillScope = SkillScope.GLOBAL,
    roots: Sequence[Path] | None = None,
) -> ManifestEntry:
    """Materialize a skill and pin it.

    The pin is, in priority order: an explicit ``version`` (frozen), a custom
    ``alias`` (moving), or the reserved ``latest`` alias by default. Aliases are
    resolved server-side. ``roots`` selects the project scope's manifest (session
    project roots); defaults to the global harness singleton.
    """
    endpoint = await asyncio.to_thread(_resolve_endpoint, config)
    if endpoint is None:
        raise RegistrySkillsError("no authenticated Mistral endpoint available")
    pin: int | str
    if version is not None:
        pin = version
    elif alias is not None:
        pin = alias
    else:
        pin = REGISTRY_LATEST_ALIAS

    async with RegistrySkillsClient(endpoint.api_base, endpoint.api_key) as client:
        if isinstance(pin, int):
            item = await client.get_skill(skill_id, version=pin)
        else:
            item = await client.get_skill(skill_id, alias=pin)

    name = item.resolved_name
    if name is None:
        raise RegistrySkillsError(f"skill {skill_id} has no usable name")
    if await _store.materialize(item, name) is None:
        raise RegistrySkillsError(f"skill {skill_id} has an empty body")

    if isinstance(pin, str) and pin != REGISTRY_LATEST_ALIAS:
        await asyncio.to_thread(_resolved.record, {(skill_id, pin): item.version})

    path = _manifest_path_for_scope(scope, roots)
    manifest = await _manifest.load(path)
    entry = ManifestEntry(
        name=name, skill_id=skill_id, version=pin, description=item.resolved_description
    )
    manifest.upsert(entry)
    await _manifest.save(path, manifest)
    return entry


def _manifest_path_for_scope(
    scope: SkillScope, roots: Sequence[Path] | None = None
) -> Path:
    if scope is SkillScope.PROJECT:
        effective_roots = (
            get_harness_files_manager().project_roots if roots is None else roots
        )
        project_paths = _manifest.project_manifest_paths_sync(effective_roots)
        if not project_paths:
            raise RegistrySkillsError(
                "no project skills manifest is available for a project-scoped pin"
            )
        path = project_paths[0]
        if not _within_project_roots(path, effective_roots):
            raise RegistrySkillsError(
                f"refusing to write a project skills manifest outside the "
                f"project root: {path}"
            )
        return path
    return _manifest.global_manifest_path()


def project_scope_available(roots: Sequence[Path] | None = None) -> bool:
    """True when a project-scoped manifest distinct from the global one exists.

    Running from the home directory collapses project and global onto the same
    file, so project scope is only offered when they are genuinely separate.
    """
    return bool(_manifest.project_manifest_paths_sync(roots))


async def list_skill_versions(
    config: VibeConfigSchema, skill_id: str
) -> list[SkillVersionInfo]:
    endpoint = await asyncio.to_thread(_resolve_endpoint, config)
    if endpoint is None:
        return []
    try:
        async with RegistrySkillsClient(endpoint.api_base, endpoint.api_key) as client:
            return await client.list_versions(skill_id)
    except RegistrySkillsError as exc:
        logger.warning("Failed to list skill versions: %s", exc.reason)
        return []


def _paths_for_scope(
    scope: SkillScope, roots: Sequence[Path] | None = None
) -> list[Path]:
    if scope is SkillScope.PROJECT:
        return _manifest.project_manifest_paths_sync(roots)
    return [_manifest.global_manifest_path()]


def _find_entry(
    name: str, scope: SkillScope, roots: Sequence[Path] | None = None
) -> ManifestEntry | None:
    found = _find_entry_with_path(name, scope, roots)
    return found[0] if found is not None else None


def _find_entry_with_path(
    name: str, scope: SkillScope, roots: Sequence[Path] | None = None
) -> tuple[ManifestEntry, Path] | None:
    """The pin and the manifest path that owns it (needed to keep reads and
    writes on the same root, e.g. when exporting a converted local skill).
    """
    for path in _paths_for_scope(scope, roots):
        entry = next(
            (e for e in _manifest.load_sync(path).skills if e.name == name), None
        )
        if entry is not None:
            return entry, path
    return None


def _find_skill_id(
    name: str, scope: SkillScope, roots: Sequence[Path] | None = None
) -> str | None:
    entry = _find_entry(name, scope, roots)
    return entry.skill_id if entry is not None else None


def _resolved_version(entry: ManifestEntry) -> int | None:
    """The concrete on-disk version this pin currently maps to, if materialized."""
    if isinstance(entry.version, int):
        return entry.version
    if entry.version == REGISTRY_LATEST_ALIAS:
        return _store.latest_materialized_sync(entry.skill_id)
    resolved = _resolved.get(entry.skill_id, entry.version)
    if resolved is not None and _store.is_materialized_sync(entry.skill_id, resolved):
        return resolved
    return _store.latest_materialized_sync(entry.skill_id)


def _is_safe_name(name: str) -> bool:
    """Whether ``name`` is a plain path component (no separators or traversal).

    The name comes from registry-resolved metadata / manifest entries, so it is
    untrusted: a value like ``../../evil`` or an absolute path would otherwise
    escape the skills root when appended to it.
    """
    return bool(name) and name not in {".", ".."} and Path(name).name == name


def _local_skill_dir(
    name: str,
    scope: SkillScope,
    manifest_path: Path | None = None,
    roots: Sequence[Path] | None = None,
) -> Path | None:
    if not _is_safe_name(name):
        logger.warning("Refusing to convert skill with unsafe name %r", name)
        return None
    if scope is SkillScope.PROJECT:
        if roots is None:
            roots = get_harness_files_manager().project_roots
        if manifest_path is not None:
            base = manifest_path.parent
        elif roots:
            base = roots[0] / ".vibe"
        else:
            return None
        target = base / "skills" / name
        if not _within_project_roots(target, roots):
            logger.warning(
                "Refusing project skill write outside the project root: %s", target
            )
            return None
        return target
    return GLOBAL_SKILLS_DIR.path / name


def _within_project_roots(base: Path, roots: Sequence[Path]) -> bool:
    """Is ``base`` inside a project root once symlinks are resolved?

    Project manifest discovery follows symlinks, so a repository-controlled
    ``.vibe`` link could otherwise redirect writes outside the workspace.
    """
    try:
        resolved = base.resolve()
    except OSError:
        return False
    for root in roots:
        try:
            if resolved.is_relative_to(root.resolve()):
                return True
        except OSError:
            continue
    return False


def convert_skill_to_local(
    name: str,
    scope: SkillScope = SkillScope.GLOBAL,
    roots: Sequence[Path] | None = None,
) -> Path | None:
    """Adopt a registry pin as a standalone local skill.

    Copies the cached content into the matching local skills dir, drops the
    registry frontmatter, and removes the registry pin. Returns the new path, or
    None when nothing is materialized or a local skill of that name already
    exists. Intended for skills that no longer exist upstream.
    """
    found = _find_entry_with_path(name, scope, roots)
    if found is None:
        return None
    entry, manifest_path = found
    version = _resolved_version(entry)
    if version is None or not _store.is_materialized_sync(entry.skill_id, version):
        return None
    target = _local_skill_dir(name, scope, manifest_path, roots)
    if target is None or target.exists():
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        _store.export_local_sync(entry.skill_id, version, target)
    except OSError:
        shutil.rmtree(target, ignore_errors=True)
        raise
    remove_skill(name, scope, roots)
    return target


async def set_skill_version(
    config: VibeConfigSchema,
    name: str,
    version: int,
    scope: SkillScope = SkillScope.GLOBAL,
    roots: Sequence[Path] | None = None,
) -> ManifestEntry | None:
    """Freeze an imported skill at a specific version, in the given scope."""
    skill_id = _find_skill_id(name, scope, roots)
    if skill_id is None:
        return None
    return await import_skill(
        config, skill_id, version=version, scope=scope, roots=roots
    )


async def set_skill_latest(
    config: VibeConfigSchema,
    name: str,
    scope: SkillScope = SkillScope.GLOBAL,
    roots: Sequence[Path] | None = None,
) -> ManifestEntry | None:
    """Switch a scope's pin to the 'latest' alias (resolved server-side)."""
    skill_id = _find_skill_id(name, scope, roots)
    if skill_id is None:
        return None
    return await import_skill(config, skill_id, scope=scope, roots=roots)


async def set_skill_alias(
    config: VibeConfigSchema,
    name: str,
    alias: str,
    scope: SkillScope = SkillScope.GLOBAL,
    roots: Sequence[Path] | None = None,
) -> ManifestEntry | None:
    """Pin a scope to a custom alias (a moving pointer like 'latest')."""
    skill_id = _find_skill_id(name, scope, roots)
    if skill_id is None:
        return None
    return await import_skill(config, skill_id, alias=alias, scope=scope, roots=roots)


def remove_skill(
    name: str,
    scope: SkillScope = SkillScope.GLOBAL,
    roots: Sequence[Path] | None = None,
) -> bool:
    """Remove a skill's pin for one scope only.

    ``GLOBAL`` removes the global pin; ``PROJECT`` removes only the project
    pin(s). A skill pinned in both scopes is removed one scope at a time.
    """
    if scope is SkillScope.PROJECT:
        effective_roots = (
            get_harness_files_manager().project_roots if roots is None else roots
        )
        paths = [
            path
            for path in _manifest.project_manifest_paths_sync(effective_roots)
            if _within_project_roots(path, effective_roots)
        ]
    else:
        paths = [_manifest.global_manifest_path()]
    removed = False
    for path in paths:
        manifest = _manifest.load_sync(path)
        if manifest.remove(name):
            _manifest.save_sync(path, manifest)
            removed = True
    return removed


async def check_updates(
    config: VibeConfigSchema, roots: Sequence[Path] | None = None
) -> list[SkillUpdate]:
    """Frozen pins whose registry latest is newer than the pinned version.

    Tracking pins are excluded: they advance to latest on every refresh, so a
    newer version is never 'available' for them, it is simply adopted.
    """
    endpoint = await asyncio.to_thread(_resolve_endpoint, config)
    if endpoint is None:
        return []
    manifests = await _load_update_manifests(roots)
    frozen_ids = {
        entry.skill_id
        for manifest in manifests
        for entry in manifest.skills
        if isinstance(entry.version, int) and not _unsafe_pin(entry)
    }
    if not frozen_ids:
        return []
    try:
        async with RegistrySkillsClient(endpoint.api_base, endpoint.api_key) as client:
            latest_by_id = await _resolve_latest_versions(client, frozen_ids)
    except RegistrySkillsError as exc:
        logger.warning("Failed to check registry skill updates: %s", exc.reason)
        return []

    updates: list[SkillUpdate] = []
    seen_names: set[str] = set()
    for manifest in manifests:
        for entry in manifest.skills:
            if entry.name in seen_names:
                continue
            seen_names.add(entry.name)
            if not isinstance(entry.version, int):
                continue
            latest = latest_by_id.get(entry.skill_id)
            if latest is not None and latest > entry.version:
                updates.append(
                    SkillUpdate(
                        name=entry.name,
                        current_version=entry.version,
                        latest_version=latest,
                    )
                )
    return updates


async def _load_update_manifests(
    roots: Sequence[Path] | None = None,
) -> list[SkillManifest]:
    """Project manifests first, so a project pin's name wins over a global one."""
    paths = [
        *await _manifest.project_manifest_paths(roots),
        _manifest.global_manifest_path(),
    ]
    return list(await asyncio.gather(*(_manifest.load(path) for path in paths)))


async def check_new_versions(
    config: VibeConfigSchema, roots: Sequence[Path] | None = None
) -> list[SkillUpdate]:
    """Like ``check_updates`` but only versions that are new *since last session*.

    Used by the session-start prompt: a frozen pin lagging behind is surfaced
    once per registry release, then recorded so it does not nag every launch.
    """
    updates = await check_updates(config, roots)
    if not updates:
        return []
    ids = await _ids_by_name(roots)
    seen = await asyncio.to_thread(_notify.load_seen)
    fresh = [
        u for u in updates if u.latest_version > seen.get(ids.get(u.name, u.name), 0)
    ]
    await asyncio.to_thread(
        _notify.mark_seen, {ids.get(u.name, u.name): u.latest_version for u in fresh}
    )
    return fresh


async def _ids_by_name(roots: Sequence[Path] | None = None) -> dict[str, str]:
    ids: dict[str, str] = {}
    for manifest in await _load_update_manifests(roots):
        for entry in manifest.skills:
            ids.setdefault(entry.name, entry.skill_id)
    return ids
