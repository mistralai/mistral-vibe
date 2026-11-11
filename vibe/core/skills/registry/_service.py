from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum, auto
from http import HTTPStatus
from pathlib import Path
import shutil
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from vibe.core.config.harness_files import get_harness_files_manager
from vibe.core.config.harness_files._paths import GLOBAL_SKILLS_DIR
from vibe.core.platform_auth import (
    PlatformCredentialFactory,
    PlatformCredentialProvider,
)
from vibe.core.platform_auth._mistral import mistral_credentials
from vibe.core.skills.models import REGISTRY_LATEST_ALIAS, SkillScope
from vibe.core.skills.registry import (
    _eviction,
    _known_manifests,
    _manifest,
    _notify,
    _resolved,
    _store,
)
from vibe.core.skills.registry._client import RegistrySkillsClient, RegistrySkillsError
from vibe.core.skills.registry._manifest import ManifestEntry, SkillManifest
from vibe.core.skills.registry.models import SkillVersionInfo
from vibe.observability.logging import logger
from vibe.utils.paths import resolves_within

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


@dataclass
class _Counts:
    """A pin the registry no longer has is skipped, not failed: no later sync can fetch
    it either.
    """

    written: int = 0
    skipped: int = 0
    failed: int = 0

    def missed(self, exc: RegistrySkillsError) -> None:
        if exc.status == HTTPStatus.NOT_FOUND:
            self.skipped += 1
        else:
            self.failed += 1


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
    credentials: PlatformCredentialProvider


def _resolve_endpoint(
    config: VibeConfigSchema, credentials: PlatformCredentialFactory | None = None
) -> _Endpoint | None:
    mistral = mistral_credentials(config, credentials)
    if mistral is None:
        logger.debug("Registry skills enabled but no Mistral credential is available")
        return None
    return _Endpoint(
        api_base=mistral.provider.api_base, credentials=mistral.credentials
    )


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
    config: VibeConfigSchema,
    roots: Sequence[Path] | None = None,
    *,
    credentials: PlatformCredentialFactory | None = None,
) -> RegistrySyncResult:
    """Session-start sync.

    Resolves every pinned skill to a concrete version and downloads any missing
    bodies into the local store. Runs so that, e.g., a teammate who clones a repo
    with a committed ``.vibe/skills.toml`` but no local cache gets the bodies.

    ``roots`` are the caller's session project roots (the agent loop's
    ``harness_files.project_roots``); when omitted the global harness singleton
    is used. SKIPPED means the registry could not be asked: the flag is off or
    no credentials are configured. With nothing to fetch the sync is OK, and a
    pin whose fetch failed makes it FAILED, so the session tries again. The
    project manifests it reads go on record for later cleanups.
    """
    if not config.experimental_enable_registry_skills:
        return RegistrySyncResult(RegistrySyncStatus.SKIPPED)
    await asyncio.to_thread(
        _known_manifests.remember, await _manifest.project_manifest_paths(roots)
    )
    endpoint = await asyncio.to_thread(_resolve_endpoint, config, credentials)
    if endpoint is None:
        return RegistrySyncResult(RegistrySyncStatus.SKIPPED)
    if not await _needs_sync(roots):
        return RegistrySyncResult(RegistrySyncStatus.OK)

    try:
        async with RegistrySkillsClient(
            endpoint.api_base, endpoint.credentials
        ) as client:
            latest_by_id = await _resolve_latest_versions(
                client, await _latest_pinned_skill_ids(roots)
            )
            counts = await _sync_pins(client, latest_by_id, roots)
    except RegistrySkillsError as exc:
        logger.warning("Failed to sync registry skills: %s", exc.reason)
        return RegistrySyncResult(RegistrySyncStatus.FAILED)
    except OSError as exc:
        logger.warning("Failed to materialize registry skills: %s", exc)
        return RegistrySyncResult(RegistrySyncStatus.FAILED)

    logger.info(
        "Synced registry skills: %d fetched, %d skipped, %d failed",
        counts.written,
        counts.skipped,
        counts.failed,
    )
    status = RegistrySyncStatus.FAILED if counts.failed else RegistrySyncStatus.OK
    return RegistrySyncResult(status, written=counts.written, skipped=counts.skipped)


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
    known: list[tuple[str, int, str]],
    seen: set[tuple[str, int]],
    sid: str,
    version: int,
    name: str,
) -> None:
    """Queue (sid, version) for batch materialization once."""
    if (sid, version) not in seen:
        seen.add((sid, version))
        known.append((sid, version, name))


async def _sync_alias(
    client: RegistrySkillsClient,
    entry: ManifestEntry,
    alias: str,
    resolved_aliases: dict[tuple[str, str], int],
    alias_seen: set[tuple[str, str]],
    counts: _Counts,
) -> None:
    """Resolve + materialize a custom alias pin, counting it in ``counts``.

    Resolved inline because ``get_skill(alias=...)`` already returns the body,
    and deduplicated so the same alias in several manifests hits the server once.
    """
    sid = entry.skill_id
    key = (sid, alias)
    if key in alias_seen:
        return
    alias_seen.add(key)
    try:
        item = await client.get_skill(sid, alias=alias)
    except RegistrySkillsError as exc:
        logger.warning(
            "Failed to resolve alias '%s' for '%s': %s", alias, entry.name, exc.reason
        )
        counts.missed(exc)
        return
    resolved_aliases[key] = item.version
    if await _store.is_materialized(sid, item.version):
        return
    if await _store.materialize(item, entry.name) is None:
        logger.debug(
            "Alias '%s' for %s resolved to v%d with an empty body",
            alias,
            sid,
            item.version,
        )
        counts.skipped += 1
        return
    counts.written += 1


async def _sync_pins(
    client: RegistrySkillsClient,
    latest_by_id: dict[str, int],
    roots: Sequence[Path] | None = None,
) -> _Counts:
    """Resolve and materialize every pin, and count them.

    Frozen ints use their number; ``latest`` uses the catalog's newest version
    (falling back to newest-on-disk if absent); custom aliases resolve
    server-side (cached in ``_resolved`` so discovery finds them offline).
    """
    known: list[tuple[str, int, str]] = []
    seen: set[tuple[str, int]] = set()
    resolved_aliases: dict[tuple[str, str], int] = {}
    alias_seen: set[tuple[str, str]] = set()
    counts = _Counts()

    for path in await _manifest_paths(roots):
        manifest = await _manifest.load(path)
        for entry in manifest.skills:
            if _unsafe_pin(entry):
                continue
            sid = entry.skill_id
            if isinstance(entry.version, int):
                _add_target(known, seen, sid, entry.version, entry.name)
                continue
            if entry.version != REGISTRY_LATEST_ALIAS:
                await _sync_alias(
                    client, entry, entry.version, resolved_aliases, alias_seen, counts
                )
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
            _add_target(known, seen, sid, version, entry.name)

    await asyncio.to_thread(_resolved.record, resolved_aliases)
    await _materialize_missing(client, known, counts)
    return counts


async def _materialize_missing(
    client: RegistrySkillsClient, targets: list[tuple[str, int, str]], counts: _Counts
) -> None:
    pending: list[tuple[str, int, str]] = []
    for target in targets:
        if not await _store.is_materialized(target[0], target[1]):
            pending.append(target)
    if not pending:
        return
    semaphore = asyncio.Semaphore(_MATERIALIZE_CONCURRENCY)

    async def fetch_one(skill_id: str, version: int, name: str) -> None:
        async with semaphore:
            try:
                item = await client.get_skill(skill_id, version=version)
            except RegistrySkillsError as exc:
                logger.warning("Failed to fetch skill '%s': %s", name, exc.reason)
                counts.missed(exc)
                return
        if await _store.materialize(item, name) is None:
            counts.skipped += 1
            return
        counts.written += 1

    await asyncio.gather(*(fetch_one(*target) for target in pending))


async def has_registry_endpoint(
    config: VibeConfigSchema, *, credentials: PlatformCredentialFactory | None = None
) -> bool:
    """Is a Mistral provider configured with a usable credential?"""
    return await asyncio.to_thread(_resolve_endpoint, config, credentials) is not None


async def list_catalog(
    config: VibeConfigSchema, *, credentials: PlatformCredentialFactory | None = None
) -> list[CatalogItem]:
    endpoint = await asyncio.to_thread(_resolve_endpoint, config, credentials)
    if endpoint is None:
        return []
    async with RegistrySkillsClient(endpoint.api_base, endpoint.credentials) as client:
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
    config: VibeConfigSchema,
    skill_id: str,
    *,
    version: int | None = None,
    credentials: PlatformCredentialFactory | None = None,
) -> str:
    """Fetch a single skill's SKILL.md body (for preview/details). No caching."""
    endpoint = await asyncio.to_thread(_resolve_endpoint, config, credentials)
    if endpoint is None:
        raise RegistrySkillsError("no authenticated Mistral endpoint available")
    async with RegistrySkillsClient(endpoint.api_base, endpoint.credentials) as client:
        item = await client.get_skill(skill_id, version=version)
    return item.skill.skill_body


async def get_skill_details(
    config: VibeConfigSchema,
    skill_id: str,
    *,
    version: int | None = None,
    credentials: PlatformCredentialFactory | None = None,
) -> SkillDetails | None:
    """Fetch a skill version's full registry object (metadata + body)."""
    endpoint = await asyncio.to_thread(_resolve_endpoint, config, credentials)
    if endpoint is None:
        return None
    try:
        async with RegistrySkillsClient(
            endpoint.api_base, endpoint.credentials
        ) as client:
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
    credentials: PlatformCredentialFactory | None = None,
) -> ManifestEntry:
    """Materialize a skill and pin it.

    The pin is, in priority order: an explicit ``version`` (frozen), a custom
    ``alias`` (moving), or the reserved ``latest`` alias by default. Aliases are
    resolved server-side. ``roots`` selects the project scope's manifest (session
    project roots); defaults to the global harness singleton. A scope pins one
    skill per name, so another shared skill pinned there under the same name is
    refused rather than replaced.
    """
    endpoint = await asyncio.to_thread(_resolve_endpoint, config, credentials)
    if endpoint is None:
        raise RegistrySkillsError("no authenticated Mistral endpoint available")
    pin: int | str
    if version is not None:
        pin = version
    elif alias is not None:
        pin = alias
    else:
        pin = REGISTRY_LATEST_ALIAS

    async with RegistrySkillsClient(endpoint.api_base, endpoint.credentials) as client:
        if isinstance(pin, int):
            item = await client.get_skill(skill_id, version=pin)
        else:
            item = await client.get_skill(skill_id, alias=pin)

    name = item.resolved_name
    if name is None:
        raise RegistrySkillsError(f"skill {skill_id} has no usable name")
    path = _manifest_path_for_scope(scope, roots)
    manifest = await _manifest.load(path)
    if any(
        pinned.name == name and pinned.skill_id != skill_id
        for pinned in manifest.skills
    ):
        raise RegistrySkillsError(
            f"another shared skill named {name!r} is pinned in {scope.value}; "
            "remove it first"
        )
    try:
        stored = await _store.materialize(item, name)
    except OSError as exc:
        reason = str(exc) or type(exc).__name__
        raise RegistrySkillsError(
            f"could not store skill {skill_id}: {reason}"
        ) from exc
    if stored is None:
        raise RegistrySkillsError(f"skill {skill_id} has an empty body")

    if isinstance(pin, str) and pin != REGISTRY_LATEST_ALIAS:
        await asyncio.to_thread(_resolved.record, {(skill_id, pin): item.version})

    entry = ManifestEntry(
        name=name, skill_id=skill_id, version=pin, description=item.resolved_description
    )
    manifest.upsert(entry)
    await _manifest.save(path, manifest)
    if scope is SkillScope.PROJECT:
        await asyncio.to_thread(_known_manifests.remember, [path])
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
        if not resolves_within(path, effective_roots):
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
    config: VibeConfigSchema,
    skill_id: str,
    *,
    credentials: PlatformCredentialFactory | None = None,
) -> list[SkillVersionInfo]:
    endpoint = await asyncio.to_thread(_resolve_endpoint, config, credentials)
    if endpoint is None:
        return []
    try:
        async with RegistrySkillsClient(
            endpoint.api_base, endpoint.credentials
        ) as client:
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


async def _pinned_versions(roots: Sequence[Path] | None = None) -> set[tuple[str, int]]:
    pinned = await asyncio.to_thread(_resolved.recorded_versions)
    known = await asyncio.to_thread(_known_manifests.remembered)
    for path in dict.fromkeys([*await _manifest_paths(roots), *known]):
        for entry in (await _manifest.load(path)).skills:
            if _unsafe_pin(entry):
                continue
            version = await asyncio.to_thread(_resolved_version, entry)
            if version is not None:
                pinned.add((entry.skill_id, version))
    return pinned


async def cleanup_candidates(
    roots: Sequence[Path] | None = None,
) -> dict[str, list[int]]:
    """Claims the day's cleanup first, so within a day of the last one it lists nothing."""
    if not await asyncio.to_thread(_eviction.claim_cleanup):
        return {}
    pinned = await _pinned_versions(roots)
    return await asyncio.to_thread(_eviction.unused_versions, pinned)


async def still_served(
    config: VibeConfigSchema,
    unused: Mapping[str, Sequence[int]],
    *,
    credentials: PlatformCredentialFactory | None = None,
) -> dict[str, list[int]]:
    """None without credentials, or when the registry cannot say."""
    latest = await _catalog_latest(config, credentials)
    return {
        skill_id: [version for version in versions if version <= latest[skill_id]]
        for skill_id, versions in unused.items()
        if skill_id in latest
    }


async def _catalog_latest(
    config: VibeConfigSchema, credentials: PlatformCredentialFactory | None
) -> dict[str, int]:
    """The registry serves every version up to the latest a listing shows, as a version
    is never changed or deleted; none without credentials, or when the listing fails.
    """
    endpoint = await asyncio.to_thread(_resolve_endpoint, config, credentials)
    if endpoint is None:
        return {}
    try:
        async with RegistrySkillsClient(
            endpoint.api_base, endpoint.credentials
        ) as client:
            items = await client.list_catalog(page_size=_PAGE_SIZE)
    except RegistrySkillsError as exc:
        logger.debug("Keeping every stored version: %s", exc.reason)
        return {}
    return {
        item.skill_id: item.metadata.latest_version or item.version for item in items
    }


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
        if not resolves_within(target, roots):
            logger.warning(
                "Refusing project skill write outside the project root: %s", target
            )
            return None
        return target
    return GLOBAL_SKILLS_DIR.path / name


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
        _store.export_local_sync(entry.skill_id, version, target, entry.name)
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
    *,
    credentials: PlatformCredentialFactory | None = None,
) -> ManifestEntry | None:
    """Freeze an imported skill at a specific version, in the given scope."""
    skill_id = _find_skill_id(name, scope, roots)
    if skill_id is None:
        return None
    return await import_skill(
        config,
        skill_id,
        version=version,
        scope=scope,
        roots=roots,
        credentials=credentials,
    )


async def set_skill_latest(
    config: VibeConfigSchema,
    name: str,
    scope: SkillScope = SkillScope.GLOBAL,
    roots: Sequence[Path] | None = None,
    *,
    credentials: PlatformCredentialFactory | None = None,
) -> ManifestEntry | None:
    """Switch a scope's pin to the 'latest' alias (resolved server-side)."""
    skill_id = _find_skill_id(name, scope, roots)
    if skill_id is None:
        return None
    return await import_skill(
        config, skill_id, scope=scope, roots=roots, credentials=credentials
    )


async def set_skill_alias(
    config: VibeConfigSchema,
    name: str,
    alias: str,
    scope: SkillScope = SkillScope.GLOBAL,
    roots: Sequence[Path] | None = None,
    *,
    credentials: PlatformCredentialFactory | None = None,
) -> ManifestEntry | None:
    """Pin a scope to a custom alias (a moving pointer like 'latest')."""
    skill_id = _find_skill_id(name, scope, roots)
    if skill_id is None:
        return None
    return await import_skill(
        config, skill_id, alias=alias, scope=scope, roots=roots, credentials=credentials
    )


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
            if resolves_within(path, effective_roots)
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
    config: VibeConfigSchema,
    roots: Sequence[Path] | None = None,
    *,
    credentials: PlatformCredentialFactory | None = None,
) -> list[SkillUpdate]:
    """Frozen pins whose registry latest is newer than the pinned version.

    Tracking pins are excluded: they advance to latest on every refresh, so a
    newer version is never 'available' for them, it is simply adopted.
    """
    endpoint = await asyncio.to_thread(_resolve_endpoint, config, credentials)
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
        async with RegistrySkillsClient(
            endpoint.api_base, endpoint.credentials
        ) as client:
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
    config: VibeConfigSchema,
    roots: Sequence[Path] | None = None,
    *,
    credentials: PlatformCredentialFactory | None = None,
) -> list[SkillUpdate]:
    """Like ``check_updates`` but only versions that are new *since last session*.

    Used by the session-start prompt: a frozen pin lagging behind is surfaced
    once per registry release, then recorded so it does not nag every launch.
    """
    updates = await check_updates(config, roots, credentials=credentials)
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
