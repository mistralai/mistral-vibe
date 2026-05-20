from __future__ import annotations

from pathlib import Path
import tomllib
from typing import Any

from vibe.core.config.layer import ConfigLayer, RawConfig
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

    async def _read_config(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        with self._path.open("rb") as f:
            return tomllib.load(f)

    async def apply(self, patch: Any, *, on_conflict: str = "cancel") -> None:
        raise NotImplementedError("UserConfigLayer.apply() is not implemented (M2)")
