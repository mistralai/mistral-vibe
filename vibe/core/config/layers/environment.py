from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError, create_model
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, SettingsConfigDict, SettingsError

from vibe.core.config.fingerprint import create_dict_fingerprint
from vibe.core.config.layer import ConfigLayer, RawConfig
from vibe.core.config.types import LayerConfigSnapshot
from vibe.observability.logging import logger


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

    A top-level field whose variables do not parse is ignored with a warning, so an
    unrelated tool exporting a colliding ``VIBE_*`` name cannot block startup. A
    nested field such as ``VIBE_SESSION_LOGGING__ENABLED`` drops its whole
    ``VIBE_SESSION_LOGGING`` group.
    """

    NAME = "environment"

    def __init__(self, *, name: str = NAME, schema: type[BaseModel]) -> None:
        super().__init__(name=name)

        self._fields = schema.model_fields
        self._settings_class = _settings_class(self._fields)

    async def _check_trust(self) -> bool:
        return True

    async def _build_config_snapshot(self) -> LayerConfigSnapshot:
        data = self._read_environment()
        fingerprint = create_dict_fingerprint(data)
        return LayerConfigSnapshot(data=data, fingerprint=fingerprint)

    async def _save_to_store(self, _next_config: RawConfig) -> str:
        raise NotImplementedError(
            "EnvironmentLayer patch persistence is not implemented yet"
        )

    def _read_environment(self) -> dict[str, Any]:
        try:
            return self._settings_class().model_dump(exclude_unset=True)
        except (SettingsError, ValidationError):
            pass
        data: dict[str, Any] = {}
        for field_name, info in self._fields.items():
            try:
                data |= _settings_class({field_name: info})().model_dump(
                    exclude_unset=True
                )
            except (SettingsError, ValidationError):
                env_name = f"VIBE_{field_name.upper()}"
                logger.warning(
                    "Ignoring unparseable %s or %s__* environment value",
                    env_name,
                    env_name,
                )
        return data


def _settings_class(fields: dict[str, FieldInfo]) -> type[BaseSettings]:
    definitions: dict[str, Any] = {
        name: (info.annotation, info) for name, info in fields.items()
    }
    return create_model("_EnvSchema", __base__=_EnvBase, **definitions)
