"""OS file events on a session's skill folders, so new skills need no /reload.

Parents are watched flat so a ``.vibe/skills`` created later is noticed.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Iterable
from contextlib import suppress
from pathlib import Path
from typing import NamedTuple

import watchfiles

from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.config.harness_files._paths import (
    GLOBAL_AGENTS_SKILLS_DIR,
    GLOBAL_SKILLS_DIR,
)
from vibe.core.paths import VIBE_HOME

SKILL_WATCH_DEBOUNCE_MS = 300
# Only while there is no folder to watch yet.
SKILL_WATCH_IDLE_POLL_S = 5.0

_SKILL_FILE = "SKILL.md"
_REGISTRY_MANIFEST = "skills.toml"
_SKILLS_DIR_NAME = "skills"
_CONFIG_DIR_NAMES = frozenset({".vibe", ".agents"})


class SkillWatchRoots(NamedTuple):
    recursive: list[Path]
    flat: list[Path]
    # Configured ``skill_paths`` not created yet, watched through their parent.
    awaited: list[Path]


def skill_watch_roots(
    harness_files: HarnessFilesManager, configured: Iterable[Path] = ()
) -> SkillWatchRoots:
    """``project_roots`` already excludes untrusted ones."""
    recursive: list[Path] = []
    configured = [path.expanduser() for path in configured]
    awaited = [path.resolve() for path in configured if not path.is_dir()]
    flat = [path.parent for path in configured if not path.is_dir()]
    candidates = [*configured]
    for root in harness_files.project_roots:
        flat.append(root)
        for config_dir in _CONFIG_DIR_NAMES:
            flat.append(root / config_dir)
            candidates.append(root / config_dir / _SKILLS_DIR_NAME)
    if "user" in harness_files.sources:
        flat.extend([VIBE_HOME.path, GLOBAL_AGENTS_SKILLS_DIR.path.parent])
        candidates.extend([GLOBAL_SKILLS_DIR.path, GLOBAL_AGENTS_SKILLS_DIR.path])
    for candidate in candidates:
        if candidate.is_dir():
            recursive.append(candidate.resolve())
    watched = set(recursive)
    flat = [
        path.resolve()
        for path in dict.fromkeys(flat)
        if path.is_dir() and path.resolve() not in watched
    ]
    return SkillWatchRoots(recursive, flat, awaited)


def is_skill_change(
    path: str, recursive: Iterable[Path], awaited: Iterable[Path] = ()
) -> bool:
    changed = Path(path)
    if changed.name in {_SKILL_FILE, _REGISTRY_MANIFEST}:
        return True
    if changed.name in _CONFIG_DIR_NAMES or changed.name == _SKILLS_DIR_NAME:
        return True
    if changed in awaited:
        return True
    return any(changed.parent == root for root in recursive)


async def watch_skill_changes(
    roots: Callable[[], SkillWatchRoots],
    *,
    debounce_ms: int = SKILL_WATCH_DEBOUNCE_MS,
    idle_poll_s: float = SKILL_WATCH_IDLE_POLL_S,
) -> AsyncGenerator[None]:
    """Yield once per debounced batch of changes; ``roots`` is re-read after each."""
    while True:
        current = await asyncio.to_thread(roots)
        if not current.recursive and not current.flat:
            await asyncio.sleep(idle_poll_s)
            continue
        changed = await _next_change(current, debounce_ms)
        if changed:
            yield


async def _next_change(roots: SkillWatchRoots, debounce_ms: int) -> bool:
    stop = asyncio.Event()
    watchers = [
        _first_batch(
            roots.recursive,
            recursive=True,
            debounce_ms=debounce_ms,
            stop=stop,
            skill_roots=roots.recursive,
        ),
        _first_batch(
            roots.flat,
            recursive=False,
            debounce_ms=debounce_ms,
            stop=stop,
            skill_roots=roots.recursive,
            awaited=roots.awaited,
        ),
    ]
    tasks = [asyncio.create_task(watcher) for watcher in watchers]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        return any(task.result() for task in done)
    finally:
        stop.set()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task


async def _first_batch(
    paths: list[Path],
    *,
    recursive: bool,
    debounce_ms: int,
    stop: asyncio.Event,
    skill_roots: list[Path],
    awaited: list[Path] | None = None,
) -> bool:
    if not paths:
        await stop.wait()
        return False
    awaited_paths = awaited or []
    async for changes in watchfiles.awatch(
        *paths,
        watch_filter=lambda _change, path: is_skill_change(
            path, skill_roots, awaited_paths
        ),
        debounce=debounce_ms,
        recursive=recursive,
        stop_event=stop,
        ignore_permission_denied=True,
    ):
        if changes:
            return True
    return False
