from __future__ import annotations

import asyncio
from pathlib import Path
import tomllib

from pydantic import BaseModel, ConfigDict, Field, ValidationError
import tomli_w

from vibe.core.config.harness_files import get_harness_files_manager
from vibe.core.logger import logger
from vibe.core.paths import VIBE_HOME
from vibe.core.skills.models import REGISTRY_LATEST_ALIAS


class ManifestEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    skill_id: str
    # Either a concrete version number (frozen pin) or an alias string such as
    # "latest" (resolved to newest server-side, never written back here).
    version: int | str = REGISTRY_LATEST_ALIAS
    description: str = ""

    @property
    def alias(self) -> str | None:
        return self.version if isinstance(self.version, str) else None


class SkillManifest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    skills: list[ManifestEntry] = Field(default_factory=list)

    def upsert(self, entry: ManifestEntry) -> None:
        self.skills = [s for s in self.skills if s.name != entry.name]
        self.skills.append(entry)

    def remove(self, name: str) -> bool:
        kept = [s for s in self.skills if s.name != name]
        removed = len(kept) != len(self.skills)
        self.skills = kept
        return removed


def global_manifest_path() -> Path:
    return VIBE_HOME.path / "skills.toml"


async def project_manifest_paths() -> list[Path]:
    """Project-scoped manifests for the open project roots.

    A root whose manifest resolves to the global manifest (e.g. running from the
    home dir, where ``~/.vibe/skills.toml`` *is* the global one) is dropped, so
    'project' scope is never an alias for global.

    Runs off the event loop since it touches the filesystem (path resolution).
    """
    return await asyncio.to_thread(_project_manifest_paths)


def _project_manifest_paths() -> list[Path]:
    global_path = global_manifest_path().resolve()
    out: list[Path] = []
    for root in get_harness_files_manager().project_roots:
        path = (root / ".vibe" / "skills.toml").resolve()
        if path == global_path or path in out:
            continue
        out.append(path)
    return out


async def load(path: Path) -> SkillManifest:
    return await asyncio.to_thread(_load, path)


def _load(path: Path) -> SkillManifest:
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except (FileNotFoundError, OSError, tomllib.TOMLDecodeError):
        return SkillManifest()
    try:
        return SkillManifest.model_validate(data)
    except ValidationError as exc:
        logger.warning("Ignoring malformed skills manifest at %s: %s", path, exc)
        return SkillManifest()


async def save(path: Path, manifest: SkillManifest) -> None:
    await asyncio.to_thread(_save, path, manifest)


def _save(path: Path, manifest: SkillManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        tomli_w.dump(manifest.model_dump(), f)
