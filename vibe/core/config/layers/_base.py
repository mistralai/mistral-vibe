from __future__ import annotations

from abc import abstractmethod
import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
import hashlib
import os
from pathlib import Path
import tempfile
import tomllib

import tomli_w

from vibe.core.config.fingerprint import (
    create_file_fingerprint,
    open_fingerprinted_file,
)
from vibe.core.config.layer import ConfigLayer, ConfigStorageError, RawConfig
from vibe.core.config.models import (
    ModelConfig,
    normalize_model_configs,
    serialize_model_configs,
)
from vibe.core.config.types import EMPTY_CONFIG_SNAPSHOT, LayerConfigSnapshot
from vibe.core.paths._vibe_home import VIBE_HOME
from vibe.core.utils.file_lock import async_file_lock

_LOCK_TIMEOUT_SECONDS = 10.0


class BaseTomlConfigLayer(ConfigLayer[RawConfig]):
    """Shared read/write logic for TOML file-backed config layers.

    Subclasses only resolve ``_target_path``; this base reads the file into a
    snapshot and persists patches atomically.
    """

    shared_store = True

    @property
    @abstractmethod
    def _target_path(self) -> Path:
        """The TOML file this layer reads from and writes to."""
        ...

    async def _build_config_snapshot(self) -> LayerConfigSnapshot:
        try:
            return await asyncio.to_thread(_read_toml_snapshot, self._target_path)
        except OSError as e:
            raise ConfigStorageError(self.name, self._target_path, "read") from e

    async def _save_to_store(self, next_config: RawConfig) -> str:
        try:
            return await asyncio.to_thread(
                _write_toml_snapshot, self._target_path, next_config
            )
        except OSError as e:
            raise ConfigStorageError(self.name, self._target_path, "write") from e

    def _store_lock(self) -> AbstractAsyncContextManager[object]:
        return _file_lock(self.name, self._target_path)


@asynccontextmanager
async def _file_lock(layer_name: str, path: Path) -> AsyncIterator[None]:
    lock_path = _lock_path(path)
    stack = AsyncExitStack()
    try:
        await stack.enter_async_context(
            async_file_lock(lock_path, timeout=_LOCK_TIMEOUT_SECONDS)
        )
    except OSError as e:
        raise ConfigStorageError(layer_name, lock_path, "lock") from e
    async with stack:
        yield


def _lock_path(path: Path) -> Path:
    # Not beside the file: a project's .vibe/ sits in its git repository.
    key = os.path.normcase(str(path.resolve()))
    digest = hashlib.sha256(key.encode()).hexdigest()[:32]
    return VIBE_HOME.path / "locks" / "config" / f"{digest}.lock"


def _read_toml_snapshot(path: Path) -> LayerConfigSnapshot:
    """Load the file into a snapshot paired with the fingerprint of its bytes.

    A concurrent save cannot spoil this. The save lands as an atomic rename, so
    the descriptor opened here keeps serving the version it opened, and the
    fingerprint is taken from that same descriptor. Data and token therefore
    always describe one another, however busy the path is.
    """
    if not path.exists():
        return EMPTY_CONFIG_SNAPSHOT
    with open_fingerprinted_file(path) as (file, fingerprint):
        data = tomllib.load(file)
    return LayerConfigSnapshot(
        data=_internal_toml_document(data), fingerprint=fingerprint
    )


def _write_toml_snapshot(path: Path, next_config: RawConfig) -> str:
    # Replace a symlink's target, not the link, so dotfiles setups keep working.
    path = path.resolve()
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
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
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
