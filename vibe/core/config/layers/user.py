from __future__ import annotations

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


class UserConfigLayer(ConfigLayer[RawConfig]):
    """Reads the user-level TOML config file. Always trusted.

    Defaults to ``~/.vibe/config.toml`` (via VIBE_HOME).
    Pass an explicit ``path`` for testing.
    """

    def __init__(self, *, path: Path | None = None, name: str = "user-toml") -> None:
        super().__init__(name=name)
        self._path = path or (VIBE_HOME.path / "config.toml")

    async def _check_trust(self) -> bool:
        return True

    async def _build_config_snapshot(self) -> LayerConfigSnapshot:
        if not self._path.exists():
            return EMPTY_CONFIG_SNAPSHOT

        with capture_stable_file(self._path) as (file, fingerprint):
            data = tomllib.load(file)

        return LayerConfigSnapshot(data=data, fingerprint=fingerprint)

    async def apply(
        self,
        patch: ConfigPatch,
        *,
        on_conflict: ConflictStrategy = ConflictStrategy.CANCEL,
    ) -> None:
        raise NotImplementedError("UserConfigLayer.apply() is not implemented (M2)")
