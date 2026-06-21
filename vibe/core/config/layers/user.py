from __future__ import annotations

import os
from pathlib import Path
import tempfile
import tomllib

import tomli_w

from vibe.core.config.fingerprint import capture_stable_file, create_file_fingerprint
from vibe.core.config.layer import ConfigLayer, RawConfig
from vibe.core.config.types import EMPTY_CONFIG_SNAPSHOT, LayerConfigSnapshot
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

    async def _save_to_store(self, next_config: RawConfig) -> str:
        if not self._path.exists():
            raise FileNotFoundError(self._path)

        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                delete=False,
            ) as tmp_file:
                tmp_path = Path(tmp_file.name)
                tomli_w.dump(next_config.model_dump(), tmp_file)
                tmp_file.flush()  # Flush Python buffers.
                os.fsync(tmp_file.fileno())  # Flush OS buffers.
                fingerprint = create_file_fingerprint(tmp_file)

            tmp_path.replace(self._path)
            tmp_path = None
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

        return fingerprint
