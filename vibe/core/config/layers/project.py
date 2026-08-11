from __future__ import annotations

import asyncio
import copy
from pathlib import Path

from vibe.core.config.layer import RawConfig
from vibe.core.config.layers._base import BaseTomlConfigLayer
from vibe.core.paths._vibe_home import VIBE_HOME
from vibe.core.trusted_folders import TrustedFoldersManager, trusted_folders_manager


class ProjectConfigLayer(BaseTomlConfigLayer):
    """Reads a project-level TOML config file.
    If no file is found in the current working directory, walks up parent directories
    until a trusted .vibe/config.toml is found.
    """

    def __init__(
        self,
        *,
        path: Path | None = None,
        name: str = "project-toml",
        trust_store: TrustedFoldersManager | None = None,
    ) -> None:
        super().__init__(name=name)
        self._root = path or Path.cwd()
        self._trust_store = trust_store or trusted_folders_manager
        self._config_file_path: Path | None = None
        self._is_set = False
        self._find_lock = asyncio.Lock()

    def __deepcopy__(self, memo: dict[int, object]) -> ProjectConfigLayer:
        copied = type(self)(
            path=self._root, name=self.name, trust_store=self._trust_store
        )
        memo[id(self)] = copied
        copied._config_file_path = self._config_file_path
        copied._is_set = self._is_set
        copied._state = copy.deepcopy(self._state, memo)
        return copied

    @property
    def config_file_path(self) -> Path | None:
        return self._config_file_path

    @property
    def is_file_discovered(self) -> bool:
        return self._is_set and self._config_file_path is not None

    @property
    def _target_path(self) -> Path:
        return self._config_file_path or (self._root / ".vibe" / "config.toml")

    async def _save_to_store(self, next_config: RawConfig) -> str:
        fingerprint = await super()._save_to_store(next_config)
        # A persist may have created the file we hadn't discovered; adopt it so
        # discovery state and trust transitions track the on-disk file.
        self._config_file_path = self._target_path
        self._is_set = True
        return fingerprint

    async def _check_trust(self) -> bool:
        await self._find_config_file()

        if self._config_file_path is None:
            return True

        return bool(self._trust_store.is_trusted(self._config_file_path.parent))

    async def _find_config_file(self) -> None:
        async with self._find_lock:
            if self._is_set:
                return
            self._config_file_path = await asyncio.to_thread(
                _discover_config_file, self._root, VIBE_HOME.path.parent
            )
            self._is_set = True


def _discover_config_file(root: Path, stop: Path) -> Path | None:
    for directory in [root, *root.parents]:
        if directory == stop:
            break
        candidate = directory / ".vibe" / "config.toml"
        if candidate.is_file():
            return candidate
    return None
