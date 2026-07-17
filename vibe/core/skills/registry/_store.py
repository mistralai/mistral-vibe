from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil
import stat
import uuid

import yaml

from vibe.core.config.harness_files._paths import GLOBAL_REGISTRY_SKILLS_CACHE_DIR
from vibe.core.logger import logger
from vibe.core.skills.parser import SkillParseError, parse_skill_markdown
from vibe.core.skills.registry.models import RegistrySkillItem
from vibe.core.utils.io import read_safe

_RESERVED_ENTRYPOINTS = frozenset({"skill.md", "skills.md"})
_REGISTRY_SOURCE = "ai-registry"


def store_root() -> Path:
    return GLOBAL_REGISTRY_SKILLS_CACHE_DIR.path / "store"


def _skill_root(skill_id: str) -> Path:
    """Resolve a skill's cache dir, guarding against unsafe ids.

    ``skill_id`` comes from the registry and is used as a single path segment.
    Anything that isn't a plain component (empty, ``.``/``..``, or containing
    separators) is rejected, so distinct ids can't collide or escape ``store/``.
    """
    if not skill_id or skill_id in {".", ".."} or Path(skill_id).name != skill_id:
        raise ValueError(f"unsafe registry skill id: {skill_id!r}")
    return store_root().resolve() / skill_id


def skill_dir(skill_id: str, version: int) -> Path:
    return _skill_root(skill_id) / str(version)


async def is_materialized(skill_id: str, version: int) -> bool:
    return await asyncio.to_thread(_is_materialized, skill_id, version)


def _is_materialized(skill_id: str, version: int) -> bool:
    return (skill_dir(skill_id, version) / "SKILL.md").is_file()


async def latest_materialized(skill_id: str) -> int | None:
    """The highest materialized version for a skill.

    Used to resolve alias pins like 'latest' to a concrete on-disk version,
    offline-safe.
    """
    return await asyncio.to_thread(_latest_materialized, skill_id)


def _latest_materialized(skill_id: str) -> int | None:
    id_dir = _skill_root(skill_id)
    if not id_dir.is_dir():
        return None
    versions: list[int] = []
    for version_dir in id_dir.iterdir():
        if not (version_dir / "SKILL.md").is_file():
            continue
        try:
            versions.append(int(version_dir.name))
        except ValueError:
            continue
    return max(versions, default=None)


async def materialize(item: RegistrySkillItem, name: str) -> Path | None:
    """Write one skill version into the global store.

    Returns its dir, or None if the body is empty.
    """
    return await asyncio.to_thread(_materialize, item, name)


def _materialize(item: RegistrySkillItem, name: str) -> Path | None:
    dest = skill_dir(item.skill_id, item.version)
    body = _strip_frontmatter(item.skill.skill_body).strip()
    if not body:
        logger.debug("Skipping registry skill '%s' with empty body", name)
        # Drop any prior cache so is_materialized doesn't report a stale hit.
        shutil.rmtree(dest, ignore_errors=True)
        return None

    # Build the whole version in a staging dir first, so a failed write never
    # leaves a partial cache.
    dest.parent.mkdir(parents=True, exist_ok=True)
    suffix = uuid.uuid4().hex
    staging = dest.parent / f".{dest.name}.tmp-{suffix}"
    backup = dest.parent / f".{dest.name}.bak-{suffix}"
    staging.mkdir()
    try:
        (staging / "SKILL.md").write_text(
            _build_skill_markdown(name, item, body), encoding="utf-8"
        )
        _write_assets(staging, item)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    # Swap in atomically: move the old version aside, move the new one in, then
    # drop the old. If the swap fails, restore the previous cache.
    had_previous = dest.exists()
    if had_previous:
        os.replace(dest, backup)
    try:
        os.replace(staging, dest)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        if had_previous:
            os.replace(backup, dest)
        raise
    if had_previous:
        shutil.rmtree(backup, ignore_errors=True)
    return dest


def _build_skill_markdown(name: str, item: RegistrySkillItem, body: str) -> str:
    description = (
        item.resolved_description or f"Workspace skill '{name}' from the AI Registry."
    )
    extra = {
        "source": _REGISTRY_SOURCE,
        "skill_id": item.skill_id,
        "version": str(item.version),
    }
    frontmatter = yaml.safe_dump(
        {"name": name, "description": description, "metadata": extra},
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )
    return f"---\n{frontmatter}---\n\n{body}\n"


def _strip_frontmatter(body: str) -> str:
    if not body.lstrip().startswith("---"):
        return body
    try:
        _, markdown_body = parse_skill_markdown(body)
    except SkillParseError:
        return body
    return markdown_body


def _write_assets(dest: Path, item: RegistrySkillItem) -> None:
    base = dest.resolve()
    for raw_path, asset in item.skill.skill_assets.items():
        target = _safe_dest(base, raw_path)
        if target is None:
            logger.debug("Skipping unsafe registry asset path %r", raw_path)
            continue
        content = asset.to_bytes()
        if content is None:
            logger.debug(
                "Skipping registry asset %r with undecodable content", raw_path
            )
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        if asset.is_executable:
            # Owner-execute only: registry content is external, so don't make it
            # group- or world-executable.
            target.chmod(target.stat().st_mode | stat.S_IXUSR)


def _safe_dest(base: Path, raw_path: str) -> Path | None:
    cleaned = raw_path.strip()
    if not cleaned:
        return None
    target = (base / cleaned).resolve()
    # Must land strictly inside the skill dir (reject the dir itself + traversal).
    if target == base or base not in target.parents:
        return None
    # Reject entrypoint names post-normalization so e.g. "sub/../SKILL.md" can't
    # slip past and overwrite the generated SKILL.md.
    if target.parent == base and target.name.casefold() in _RESERVED_ENTRYPOINTS:
        return None
    return target


async def export_local(skill_id: str, version: int, target: Path) -> None:
    """Copy a materialized version into ``target`` as a standalone local skill.

    The registry frontmatter (source/skill_id/version) is dropped so the result
    reads as a plain, user-owned skill.
    """
    await asyncio.to_thread(_export_local, skill_id, version, target)


def _export_local(skill_id: str, version: int, target: Path) -> None:
    shutil.copytree(skill_dir(skill_id, version), target)
    skill_file = target / "SKILL.md"
    try:
        frontmatter, body = parse_skill_markdown(read_safe(skill_file).text)
    except SkillParseError:
        return  # leave the copy untouched; it already carries valid frontmatter
    front = yaml.safe_dump(
        {
            "name": frontmatter.get("name") or target.name,
            "description": frontmatter.get("description") or "",
        },
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )
    skill_file.write_text(f"---\n{front}---\n\n{body.strip()}\n", encoding="utf-8")


async def prune(active: set[tuple[str, int]]) -> None:
    """Remove store entries that are not in the active (skill_id, version) set."""
    await asyncio.to_thread(_prune, active)


def _prune(active: set[tuple[str, int]]) -> None:
    root = store_root()
    if not root.is_dir():
        return
    for id_dir in root.iterdir():
        if not id_dir.is_dir():
            continue
        for version_dir in id_dir.iterdir():
            if not version_dir.is_dir():
                continue
            try:
                version = int(version_dir.name)
            except ValueError:
                continue
            if (id_dir.name, version) not in active:
                shutil.rmtree(version_dir, ignore_errors=True)
        if id_dir.is_dir() and not any(id_dir.iterdir()):
            try:
                id_dir.rmdir()
            except OSError:
                pass
