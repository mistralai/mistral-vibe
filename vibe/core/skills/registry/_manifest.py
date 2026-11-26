from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
import tomllib

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_core import ErrorDetails
import tomli_w

from vibe.core.config.harness_files import get_harness_files_manager
from vibe.core.paths import VIBE_HOME
from vibe.core.skills.models import ManifestEntry
from vibe.observability.logging import logger


class SkillManifest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    skills: list[ManifestEntry] = Field(default_factory=list)

    def upsert(self, entry: ManifestEntry) -> None:
        self.skills = [
            s
            for s in self.skills
            if s.name != entry.name and s.skill_id != entry.skill_id
        ]
        self.skills.append(entry)

    def remove(self, name: str) -> bool:
        kept = [s for s in self.skills if s.name != name]
        removed = len(kept) != len(self.skills)
        self.skills = kept
        return removed


def global_manifest_path() -> Path:
    return VIBE_HOME.path / "skills.toml"


async def project_manifest_paths(roots: Sequence[Path] | None = None) -> list[Path]:
    """Project-scoped manifests for the open project roots.

    ``roots`` should be the caller's session project roots (e.g. the agent
    loop's ``harness_files.project_roots``); when omitted it falls back to the
    global harness singleton. A root whose manifest resolves to the global
    manifest (e.g. running from the home dir, where ``~/.vibe/skills.toml`` *is*
    the global one) is dropped, so 'project' scope is never an alias for global.

    Runs off the event loop since it touches the filesystem (path resolution).
    """
    return await asyncio.to_thread(_project_manifest_paths, roots)


def _project_manifest_paths(roots: Sequence[Path] | None = None) -> list[Path]:
    if roots is None:
        roots = get_harness_files_manager().project_roots
    global_path = global_manifest_path().resolve()
    out: list[Path] = []
    for root in roots:
        path = (root / ".vibe" / "skills.toml").resolve()
        if path == global_path or path in out:
            continue
        out.append(path)
    return out


class ManifestReadError(Exception):
    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"Cannot read {path}: {reason}")
        self.path = path
        self.reason = reason


def read_sync(path: Path) -> SkillManifest:
    """Raises ``ManifestReadError`` for a manifest that exists but cannot be read,
    so a writer never replaces the pins it could not see.
    """
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        return SkillManifest()
    except OSError as exc:
        raise ManifestReadError(path, exc.strerror or str(exc)) from exc
    except tomllib.TOMLDecodeError as exc:
        raise ManifestReadError(path, f"invalid TOML: {exc}") from exc
    try:
        return SkillManifest.model_validate(data)
    except ValidationError as exc:
        reason = "; ".join(_describe(error) for error in exc.errors())
        raise ManifestReadError(path, reason) from exc


def _describe(error: ErrorDetails) -> str:
    where = ".".join(str(part) for part in error["loc"])
    return f"{where}: {error['msg']}" if where else error["msg"]


async def read(path: Path) -> SkillManifest:
    return await asyncio.to_thread(read_sync, path)


async def load(path: Path) -> SkillManifest:
    return await asyncio.to_thread(_load, path)


def _load(path: Path) -> SkillManifest:
    try:
        return read_sync(path)
    except ManifestReadError as exc:
        logger.warning("Ignoring unreadable skills manifest: %s", exc)
        return SkillManifest()


async def save(path: Path, manifest: SkillManifest) -> None:
    await asyncio.to_thread(_save, path, manifest)


def _save(path: Path, manifest: SkillManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        tomli_w.dump(manifest.model_dump(), f)


def load_sync(path: Path) -> SkillManifest:
    return _load(path)


def save_sync(path: Path, manifest: SkillManifest) -> None:
    _save(path, manifest)


def project_manifest_paths_sync(roots: Sequence[Path] | None = None) -> list[Path]:
    return _project_manifest_paths(roots)


def pinned_names_sync(project_roots: Sequence[Path]) -> set[str]:
    paths = [global_manifest_path(), *_project_manifest_paths(project_roots)]
    return {entry.name for path in paths for entry in _load(path).skills}
