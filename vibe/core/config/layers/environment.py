from __future__ import annotations

from typing import Any

from pydantic import BaseModel, TypeAdapter, ValidationError, create_model
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from vibe.core.config.fingerprint import create_dict_fingerprint
from vibe.core.config.layer import ConfigLayer, RawConfig
from vibe.core.config.types import LayerConfigSnapshot
from vibe.observability.logging import logger

_JSON_ADAPTER = TypeAdapter(Any)


class _CatalogEnvSettingsSource(EnvSettingsSource):
    def prepare_field_value(
        self, field_name: str, field: FieldInfo, value: Any, value_is_complex: bool
    ) -> Any:
        if field_name in {"models", "providers"} and isinstance(value, str):
            try:
                _JSON_ADAPTER.validate_json(value)
            except ValidationError:
                logger.warning(
                    "Ignoring unparseable VIBE_%s environment value: expected JSON",
                    field_name.upper(),
                )
                value = None
        return super().prepare_field_value(field_name, field, value, value_is_complex)


class _EnvBase(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VIBE_",
        case_sensitive=False,
        env_nested_delimiter="__",
        env_ignore_empty=True,
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            _CatalogEnvSettingsSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
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
