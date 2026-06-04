from __future__ import annotations

from typing import Any

from pydantic import BaseModel, create_model
from pydantic_settings import BaseSettings, SettingsConfigDict

from vibe.core.config.layer import ConfigLayer, RawConfig


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

    async def _read_config(self) -> dict[str, Any]:
        return self._settings_class().model_dump(exclude_unset=True)

    async def apply(self, patch: Any, *, on_conflict: str = "cancel") -> None:
        raise NotImplementedError("EnvironmentLayer.apply() is not implemented (M2)")
