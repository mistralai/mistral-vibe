from __future__ import annotations

from collections.abc import Mapping
import copy
from pathlib import Path
from typing import Any

from vibe.core.config.fingerprint import create_dict_fingerprint
from vibe.core.config.layer import ConfigLayer, RawConfig
from vibe.core.config.layers._base import BaseTomlConfigLayer, _internal_toml_document
from vibe.core.config.types import LayerConfigSnapshot
from vibe.core.paths._vibe_home import VIBE_HOME


class UserConfigLayer(BaseTomlConfigLayer):
    """Reads the user-level TOML config file. Always trusted.

    Defaults to ``~/.vibe/config.toml`` (via VIBE_HOME).
    Pass an explicit ``path`` for testing.
    """

    NAME = "user-toml"

    def __init__(self, *, path: Path | None = None, name: str = NAME) -> None:
        super().__init__(name=name)
        self._path = path or (VIBE_HOME.path / "config.toml")

    @property
    def _target_path(self) -> Path:
        return self._path

    async def _check_trust(self) -> bool:
        return True


class SuppliedUserConfigLayer(ConfigLayer[RawConfig]):
    """The user config an embedding host supplies, in place of the user file.

    ``data`` is the document the user config file would hold, as parsed TOML,
    so it loads and migrates exactly as that file would. Always trusted. Saves
    stay in memory: nothing is written to disk.
    """

    NAME = "user-supplied"

    def __init__(self, *, data: Mapping[str, Any], name: str = NAME) -> None:
        super().__init__(name=name)
        self._data = _internal_toml_document(copy.deepcopy(dict(data)))

    async def _check_trust(self) -> bool:
        return True

    async def _build_config_snapshot(self) -> LayerConfigSnapshot:
        data = copy.deepcopy(self._data)
        return LayerConfigSnapshot(data=data, fingerprint=create_dict_fingerprint(data))

    async def _save_to_store(self, next_config: RawConfig) -> str:
        self._data = copy.deepcopy(next_config.model_dump())
        return create_dict_fingerprint(self._data)
