from __future__ import annotations

from pathlib import Path

from vibe.core.config.layers._base import BaseTomlConfigLayer
from vibe.core.paths._vibe_home import VIBE_HOME


class UserConfigLayer(BaseTomlConfigLayer):
    """Reads the user-level TOML config file. Always trusted.

    Defaults to ``~/.vibe/config.toml`` (via VIBE_HOME).
    Pass an explicit ``path`` for testing.
    """

    def __init__(self, *, path: Path | None = None, name: str = "user-toml") -> None:
        super().__init__(name=name)
        self._path = path or (VIBE_HOME.path / "config.toml")

    @property
    def _target_path(self) -> Path:
        return self._path

    async def _check_trust(self) -> bool:
        return True
