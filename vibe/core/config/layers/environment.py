from __future__ import annotations

from typing import TYPE_CHECKING, Any, get_args

from pydantic import BaseModel, ValidationError, create_model
from pydantic.fields import FieldInfo
from pydantic_core.core_schema import ErrorType
from pydantic_settings import BaseSettings, SettingsConfigDict, SettingsError

from vibe.core.config.fingerprint import create_dict_fingerprint
from vibe.core.config.layer import ConfigLayer, RawConfig
from vibe.core.config.types import LayerConfigSnapshot
from vibe.observability.logging import logger

if TYPE_CHECKING:
    from pydantic_core import ErrorDetails


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
        self._load_warnings: tuple[str, ...] = ()

    @property
    def load_warnings(self) -> tuple[str, ...]:
        return self._load_warnings

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
        self._load_warnings = ()
        try:
            return self._settings_class().model_dump(exclude_unset=True)
        except (SettingsError, ValidationError):
            pass
        data: dict[str, Any] = {}
        warnings: list[str] = []
        for field_name, info in self._fields.items():
            try:
                data |= _settings_class({field_name: info})().model_dump(
                    exclude_unset=True
                )
            except (SettingsError, ValidationError) as error:
                env_name = f"VIBE_{field_name.upper()}"
                warning = (
                    f"Ignoring unparseable {env_name} or {env_name}__* environment"
                    f" value{_reasons(error)}"
                )
                logger.warning("%s", warning)
                warnings.append(warning)
        self._load_warnings = tuple(warnings)
        return data


def _reasons(error: SettingsError | ValidationError) -> str:
    if not isinstance(error, ValidationError):
        return ""
    reasons = "; ".join(
        f"{'.'.join(map(str, detail['loc']))}: {_input_free_message(detail)}"
        for detail in error.errors(include_input=False, include_url=False)
    )
    return f": {reasons}"


def _input_free_message(detail: ErrorDetails) -> str:
    # A variable can hold a secret: a message filled from context or written
    # by a validator can quote it, so only pydantic's fixed messages are shown.
    if "ctx" in detail or detail["type"] not in _BUILTIN_ERROR_TYPES:
        return detail["type"]
    return detail["msg"]


_BUILTIN_ERROR_TYPES = frozenset(get_args(ErrorType))


def _settings_class(fields: dict[str, FieldInfo]) -> type[BaseSettings]:
    definitions: dict[str, Any] = {
        name: (info.annotation, info) for name, info in fields.items()
    }
    return create_model("_EnvSchema", __base__=_EnvBase, **definitions)
