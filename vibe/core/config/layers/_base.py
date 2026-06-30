from __future__ import annotations

from abc import abstractmethod
import os
from pathlib import Path
import tempfile
import tomllib

import tomli_w

from vibe.core.config.fingerprint import capture_stable_file, create_file_fingerprint
from vibe.core.config.layer import ConfigLayer, RawConfig
from vibe.core.config.types import EMPTY_CONFIG_SNAPSHOT, LayerConfigSnapshot


class BaseTomlConfigLayer(ConfigLayer[RawConfig]):
    """Shared read/write logic for TOML file-backed config layers.

    Subclasses only resolve ``_target_path``; this base reads the file into a
    snapshot and persists patches atomically.
    """

    @property
    @abstractmethod
    def _target_path(self) -> Path:
        """The TOML file this layer reads from and writes to."""
        ...

    async def _build_config_snapshot(self) -> LayerConfigSnapshot:
        path = self._target_path
        if not path.exists():
            return EMPTY_CONFIG_SNAPSHOT

        with capture_stable_file(path) as (file, fingerprint):
            data = tomllib.load(file)

        return LayerConfigSnapshot(data=data, fingerprint=fingerprint)

    async def _save_to_store(self, next_config: RawConfig) -> str:
        path = self._target_path
        path.parent.mkdir(parents=True, exist_ok=True)

        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as tmp_file:
                tmp_path = Path(tmp_file.name)
                tomli_w.dump(next_config.model_dump(), tmp_file)
                tmp_file.flush()  # Flush Python buffers.
                os.fsync(tmp_file.fileno())  # Flush OS buffers.
                fingerprint = create_file_fingerprint(tmp_file)

            tmp_path.replace(path)
            tmp_path = None
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

        return fingerprint
