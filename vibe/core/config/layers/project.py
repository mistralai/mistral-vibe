from __future__ import annotations

import asyncio
from pathlib import Path
import tomllib

from vibe.core.config.fingerprint import capture_stable_file
from vibe.core.config.layer import ConfigLayer, RawConfig
from vibe.core.config.patch import ConfigPatch
from vibe.core.config.types import (
    EMPTY_CONFIG_SNAPSHOT,
    ConflictStrategy,
    LayerConfigSnapshot,
)
from vibe.core.paths._vibe_home import VIBE_HOME
from vibe.core.trusted_folders import trusted_folders_manager


class ProjectConfigLayer(ConfigLayer[RawConfig]):
    """Reads a project-level TOML config file.
    If no file is found in the current working directory, walks up parent directories
    until a trusted .vibe/config.toml is found.
    """

    def __init__(self, *, path: Path | None = None, name: str = "project-toml") -> None:
        super().__init__(name=name)
        self._root = path or Path.cwd()
        self._config_file_path: Path | None = None
        self._is_set = False
        self._find_lock = asyncio.Lock()

    @property
    def config_file_path(self) -> Path | None:
        return self._config_file_path

    async def _check_trust(self) -> bool:
        await self._find_config_file()

        if self._config_file_path is None:
            return True

        return bool(trusted_folders_manager.is_trusted(self._config_file_path.parent))

    async def _build_config_snapshot(self) -> LayerConfigSnapshot:
        if self._config_file_path is None or not self._config_file_path.exists():
            return EMPTY_CONFIG_SNAPSHOT

        with capture_stable_file(self._config_file_path) as (file, fingerprint):
            data = tomllib.load(file)

        return LayerConfigSnapshot(data=data, fingerprint=fingerprint)

    async def _on_trust_changed(self, old: bool | None, new: bool | None) -> None:
        if new is None or self._config_file_path is None:
            return

        if new:
            trusted_folders_manager.add_trusted(self._config_file_path.parent)
        else:
            trusted_folders_manager.add_untrusted(self._config_file_path.parent)

    async def grant_trust(self) -> None:
        await self._find_config_file()
        if self._config_file_path is None:
            return

        await super().grant_trust()

    async def revoke_trust(self) -> None:
        await self._find_config_file()
        if self._config_file_path is None:
            return

        await super().revoke_trust()

    async def apply(
        self,
        patch: ConfigPatch,
        *,
        on_conflict: ConflictStrategy = ConflictStrategy.CANCEL,
    ) -> None:
        raise NotImplementedError("ProjectConfigLayer.apply() is not implemented (M2)")

    async def _find_config_file(self) -> None:
        async with self._find_lock:
            if self._is_set:
                return

            for directory in [self._root, *self._root.parents]:
                if directory == VIBE_HOME.path.parent:
                    break

                candidate = directory / ".vibe" / "config.toml"
                if candidate.is_file():
                    self._config_file_path = candidate
                    break

            self._is_set = True
