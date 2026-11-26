from __future__ import annotations

import asyncio
from collections.abc import Iterable, Iterator
from contextlib import contextmanager, suppress
from datetime import timedelta
import os
from pathlib import Path
import shutil
import stat
import time
import uuid

import yaml

from vibe.core.config.harness_files._paths import GLOBAL_REGISTRY_SKILLS_CACHE_DIR
from vibe.core.skills.models import DISABLE_MODEL_INVOCATION_FIELD
from vibe.core.skills.parser import SkillParseError, parse_skill_markdown
from vibe.core.skills.registry.models import RegistrySkillItem, pinned_name
from vibe.core.utils import file_lock
from vibe.observability.logging import logger
from vibe.utils.io import read_safe

_RESERVED_ENTRYPOINTS = frozenset({"skill.md", "skills.md"})
_REGISTRY_SOURCE = "ai-registry"

STORE_LOCK_WAIT = timedelta(seconds=10)

LEFTOVER_AFTER = timedelta(hours=1)

_LOCK_RETRY_EVERY = 0.05

_STAGING = ".tmp-"
_TRASH = ".trash-"

type Version = tuple[str, int]


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


def is_safe_skill_id(skill_id: str) -> bool:
    """Whether ``skill_id`` is a usable single path segment (see ``_skill_root``).

    Lets callers skip an unsafe id from a committed manifest instead of letting
    the ValueError from the path helpers crash them.
    """
    try:
        _skill_root(skill_id)
    except ValueError:
        return False
    return True


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


def stored_versions(skill_id: str) -> list[int]:
    """Only directories named for their number and holding a ``SKILL.md``: a broken
    write is not a version.
    """
    stored: list[int] = []
    for entry in _skill_root(skill_id).iterdir():
        version = _version_number(entry.name)
        if version is not None and (entry / "SKILL.md").is_file():
            stored.append(version)
    return sorted(stored)


def _version_number(name: str) -> int | None:
    if not (name.isascii() and name.isdigit()):
        return None
    version = int(name)
    return version if str(version) == name else None


async def materialize(item: RegistrySkillItem, name: str) -> Path | None:
    """Write one skill version into the global store, and mark it used.

    Returns its dir, or None if the body is empty. A version already on disk is
    left as it is: a registry version never changes, and a session may be
    reading it. It is marked used all the same, so a cleanup keeps the version
    someone just pinned.
    """
    return await asyncio.to_thread(_materialize, item, name)


def _materialize(item: RegistrySkillItem, name: str) -> Path | None:
    dest = skill_dir(item.skill_id, item.version)
    body = _strip_frontmatter(item.skill.skill_body).strip()
    if not body and not _is_materialized(item.skill_id, item.version):
        logger.debug("Skipping registry skill '%s' with empty body", name)
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    with version_lock(dest):
        if not _is_materialized(item.skill_id, item.version):
            if not body:
                return None
            _write(dest, name, item, body)
        mark_used(item.skill_id, item.version)
    return dest


@contextmanager
def version_lock(dest: Path, *, wait: timedelta | None = None) -> Iterator[None]:
    """The lock file sits beside the version under a dot name, which no reader takes for
    a version.
    """
    with locked(
        dest.parent / f".{dest.name}.lock",
        wait=wait,
        description=f"version {dest.name} of {dest.parent.name}",
    ):
        yield


@contextmanager
def locked(
    path: Path, *, wait: timedelta | None = None, description: str | None = None
) -> Iterator[None]:
    """A zero ``wait`` tries once. The file stays once released: removing it would let
    two holders lock two different files.
    """
    deadline = (
        time.monotonic() + (STORE_LOCK_WAIT if wait is None else wait).total_seconds()
    )
    with path.open("a+b") as handle:
        while True:
            try:
                file_lock.acquire_file_lock(handle)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise BlockingIOError(
                        f"could not lock {description or path}"
                    ) from exc
            time.sleep(_LOCK_RETRY_EVERY)
        try:
            yield
        finally:
            file_lock.release_file_lock(handle)


def used_marker(skill_id: str, version: int) -> Path:
    """Its modification time is when a session last used the version."""
    return _skill_root(skill_id) / f".{version}.used"


def mark_used(skill_id: str, version: int) -> None:
    with suppress(OSError, ValueError):
        used_marker(skill_id, version).touch()


def unmark(skill_id: str, version: int) -> None:
    with suppress(OSError, ValueError):
        used_marker(skill_id, version).unlink(missing_ok=True)


def mark_versions_used(versions: Iterable[Version]) -> bool:
    """Each is marked before it is checked, so a cleanup moving it aside meanwhile sees
    the mark and puts it back; the marker of one gone goes too.
    """
    stored = True
    for skill_id, version in versions:
        mark_used(skill_id, version)
        if not _is_materialized(skill_id, version):
            unmark(skill_id, version)
            stored = False
    return stored


def _write(dest: Path, name: str, item: RegistrySkillItem, body: str) -> None:
    """The staged copy moves into place in one rename, so a reader never sees half of
    it.
    """
    staging = _stage(dest, name, item, body)
    try:
        _clear(dest)
        os.replace(staging, dest)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _clear(dest: Path) -> None:
    """Moved aside in one rename, then deleted, so a failed delete never leaves it in
    the way.
    """
    if not dest.exists(follow_symlinks=False):
        return
    delete(move_aside(dest))


def move_aside(dest: Path) -> Path:
    """To a dot name beside it that no reader takes for a version."""
    trash = dest.parent / f".{dest.name}{_TRASH}{uuid.uuid4().hex}"
    os.replace(dest, trash)
    return trash


def staging_path(path: Path) -> Path:
    """A dot name, which no reader takes for a version or a record."""
    return path.parent / f".{path.name}{_STAGING}{uuid.uuid4().hex}"


def sweep(directory: Path) -> None:
    """What a write staged under :data:`LEFTOVER_AFTER` ago may belong to a write still
    running, so it stays.
    """
    for entry in directory.iterdir():
        if not entry.name.startswith("."):
            continue
        if _TRASH in entry.name or (
            _STAGING in entry.name and older_than(entry, LEFTOVER_AFTER)
        ):
            delete(entry)


def older_than(path: Path, age: timedelta) -> bool:
    """One that cannot be read is not."""
    try:
        return path.lstat().st_mtime < time.time() - age.total_seconds()
    except OSError:
        return False


def delete(path: Path) -> None:
    """A link goes as itself, never what it points to; what cannot be deleted stays."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
        return
    with suppress(OSError):
        path.unlink(missing_ok=True)


def _stage(dest: Path, name: str, item: RegistrySkillItem, body: str) -> Path:
    staging = staging_path(dest)
    staging.mkdir()
    try:
        (staging / "SKILL.md").write_text(
            _build_skill_markdown(name, item, body), encoding="utf-8"
        )
        _write_assets(staging, item)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return staging


def _invocation_policy_metadata(frontmatter: dict[str, object]) -> dict[str, bool]:
    value = frontmatter.get(DISABLE_MODEL_INVOCATION_FIELD)
    if not isinstance(value, bool):
        return {}
    return {DISABLE_MODEL_INVOCATION_FIELD: value}


def _build_skill_markdown(name: str, item: RegistrySkillItem, body: str) -> str:
    description = item.resolved_description or f"Shared workspace skill '{name}'."
    extra = {
        "source": _REGISTRY_SOURCE,
        "skill_id": item.skill_id,
        "version": str(item.version),
    }
    generated_metadata: dict[str, object] = {
        "name": name,
        "description": description,
        "metadata": extra,
    }
    try:
        source_metadata, _ = parse_skill_markdown(item.skill.skill_body)
    except SkillParseError:
        source_metadata = {}
    generated_metadata.update(_invocation_policy_metadata(source_metadata))
    frontmatter = yaml.safe_dump(
        generated_metadata,
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
            target.chmod(target.stat().st_mode | stat.S_IXUSR)


def _safe_dest(base: Path, raw_path: str) -> Path | None:
    cleaned = raw_path.strip()
    if not cleaned:
        return None
    target = (base / cleaned).resolve()
    if target == base or base not in target.parents:
        return None
    if target.parent == base and target.name.casefold() in _RESERVED_ENTRYPOINTS:
        return None
    return target


async def export_local(skill_id: str, version: int, target: Path, name: str) -> None:
    """Copy a materialized version into ``target`` as a standalone local skill
    named as the pin ``name`` goes by.

    The registry frontmatter (source/skill_id/version) is dropped so the result
    reads as a plain, user-owned skill.
    """
    await asyncio.to_thread(_export_local, skill_id, version, target, name)


def _export_local(skill_id: str, version: int, target: Path, name: str) -> None:
    shutil.copytree(skill_dir(skill_id, version), target)
    skill_file = target / "SKILL.md"
    try:
        frontmatter, body = parse_skill_markdown(read_safe(skill_file).text)
    except SkillParseError:
        return
    stored = str(frontmatter.get("name") or target.name)
    exported_metadata: dict[str, object] = {
        "name": pinned_name(name, stored),
        "description": frontmatter.get("description") or "",
    }
    exported_metadata.update(_invocation_policy_metadata(frontmatter))
    front = yaml.safe_dump(
        exported_metadata, default_flow_style=False, allow_unicode=True, sort_keys=False
    )
    skill_file.write_text(f"---\n{front}---\n\n{body.strip()}\n", encoding="utf-8")


def is_materialized_sync(skill_id: str, version: int) -> bool:
    return _is_materialized(skill_id, version)


def latest_materialized_sync(skill_id: str) -> int | None:
    return _latest_materialized(skill_id)


def export_local_sync(skill_id: str, version: int, target: Path, name: str) -> None:
    _export_local(skill_id, version, target, name)
