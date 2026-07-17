from __future__ import annotations

from abc import abstractmethod
from collections.abc import Mapping
import os
from pathlib import Path
import tempfile
import tomllib

import tomli_w

from vibe.core.config.fingerprint import capture_stable_file, create_file_fingerprint
from vibe.core.config.layer import ConfigLayer, RawConfig
from vibe.core.config.models import (
    ModelConfig,
    normalize_model_configs,
    serialize_model_configs,
)
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

        data = _internal_toml_document(data)
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
                tomli_w.dump(_canonical_toml_document(next_config), tmp_file)
                tmp_file.flush()  # Flush Python buffers.
                os.fsync(tmp_file.fileno())  # Flush OS buffers.
                fingerprint = create_file_fingerprint(tmp_file)

            tmp_path.replace(path)
            tmp_path = None
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

        return fingerprint


def _canonical_toml_document(config: RawConfig) -> dict[str, object]:
    """Persist model maps as legacy [[models]] arrays for extension compatibility."""
    data = config.model_dump()
    models = data.get("models")
    if _is_model_config_mapping(models):
        data["models"] = serialize_model_configs(models)
    return data


def _internal_toml_document(data: dict[str, object]) -> dict[str, object]:
    """Load TOML model arrays as alias maps so patches can address models by key."""
    models = data.get("models")
    if _is_model_config_sequence(models) or _is_model_config_mapping(models):
        data = dict(data)
        data["models"] = normalize_model_configs(models)
    return data


def _is_model_config_sequence(value: object) -> bool:
    if not isinstance(value, list):
        return False
    return all(
        isinstance(model, Mapping) or isinstance(model, ModelConfig) for model in value
    )


def _is_model_config_mapping(value: object) -> bool:
    if not isinstance(value, Mapping) or not value:
        return False
    return all(
        isinstance(model, Mapping) or isinstance(model, ModelConfig)
        for model in value.values()
    )
