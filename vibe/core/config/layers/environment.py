from __future__ import annotations

from typing import Any

from pydantic import BaseModel, create_model
from pydantic_settings import BaseSettings, SettingsConfigDict

from vibe.core.config.fingerprint import create_dict_fingerprint
from vibe.core.config.layer import ConfigLayer, RawConfig
from vibe.core.config.types import LayerConfigSnapshot


class _EnvBase(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VIBE_",
        case_sensitive=False,
        env_nested_delimiter="__",
        env_ignore_empty=True,
        extra="ignore",
    )


class EnvironmentLayer(ConfigLayer[RawConfig]):
    """Reads VIBE_* env vars via pydantic-settings, which handles type coercion
    and validation against the schema.
    """

    def __init__(self, *, name: str = "environment", schema: type[BaseModel]) -> None:
        super().__init__(name=name)

        fields: dict[str, Any] = {
            field_name: (info.annotation, info)
            for field_name, info in schema.model_fields.items()
        }
        self._settings_class: type[BaseSettings] = create_model(
            "_EnvSchema", __base__=_EnvBase, **fields
        )

    async def _check_trust(self) -> bool:
        return True

    async def _build_config_snapshot(self) -> LayerConfigSnapshot:
        data = self._settings_class().model_dump(exclude_unset=True)
        fingerprint = create_dict_fingerprint(data)
        return LayerConfigSnapshot(data=data, fingerprint=fingerprint)

    async def _save_to_store(self, _next_config: RawConfig) -> str:
        raise NotImplementedError(
            "EnvironmentLayer patch persistence is not implemented yet"
        )
