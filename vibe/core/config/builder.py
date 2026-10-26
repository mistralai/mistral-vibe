from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable, Mapping
import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from pydantic import BeforeValidator, ValidationError
from pydantic.fields import FieldInfo
from pydantic_core import ErrorDetails

from vibe.core.config.layer import (
    ConfigLayer,
    EmptyLayerError,
    RawConfig,
    UntrustedLayerError,
)
from vibe.core.config.schema import ConfigFragment, ConfigSchema, MergeFieldMetadata
from vibe.core.utils.merge import MergeStrategy

if TYPE_CHECKING:
    from vibe.core.config.harness_files import HarnessFilesManager


class ConfigMergeError(ValueError):
    def __init__(
        self, field_name: str, layer_name: str, expected_type: str, value: Any
    ) -> None:
        actual_type = "dictionary" if isinstance(value, dict) else type(value).__name__
        message = (
            f"Invalid configuration: {field_name} from {layer_name} must be a "
            f"{expected_type}, not a {actual_type}."
        )
        if field_name == "mcp_servers" and isinstance(value, dict):
            message += " Use [[mcp_servers]] instead of [mcp_servers.<name>]."
        super().__init__(message)


class InvalidConfigError(ValueError):
    """The merged config breaks a rule of its schema, which the user can fix.

    Carries pydantic's errors as one readable issue per problem, without the
    error type and input dump of ``str(ValidationError)``, so a client can show
    it as a configuration error.
    """

    @classmethod
    def from_validation_error(cls, err: ValidationError) -> InvalidConfigError:
        return cls("; ".join(_config_issue(issue) for issue in err.errors()))


def _config_issue(issue: ErrorDetails) -> str:
    """One refused setting, named once, without pydantic's ``Value error,``."""
    message = issue["msg"].removeprefix("Value error, ")
    setting = ".".join(str(part) for part in issue["loc"])
    if not setting or message.startswith(f"{setting}:"):
        return message
    return f"{setting}: {message}"


@dataclass(frozen=True, slots=True)
class _LayerData:
    name: str
    data: dict[str, Any]


class ConfigBuilder[S: ConfigSchema]:
    """Collects layers and merges them into an immutable Config[S]."""

    def __init__(
        self, schema: type[S], *, harness_files: HarnessFilesManager | None = None
    ) -> None:
        self._schema = schema
        # The session's files, which the key check finds its custom tools with.
        self._harness_files = harness_files
        self._layers: list[ConfigLayer[RawConfig]] = []
        self._lock = asyncio.Lock()

    def add_layer(self, layer: ConfigLayer[RawConfig]) -> None:
        self._layers.append(layer)

    def add_layers(self, layers: list[ConfigLayer[RawConfig]]) -> None:
        self._layers.extend(layers)

    def insert_layer(self, layer: ConfigLayer[RawConfig], index: int) -> None:
        self._layers.insert(index, layer)

    def remove_layer(self, index: int) -> ConfigLayer[RawConfig]:
        return self._layers.pop(index)

    @property
    def layers(self) -> list[ConfigLayer[RawConfig]]:
        return self._layers

    def copy(self) -> ConfigBuilder[S]:
        """Return a new builder for the same schema with deep-copied layers."""
        new_builder = ConfigBuilder(self._schema, harness_files=self._harness_files)
        new_builder.add_layers([copy.deepcopy(layer) for layer in self._layers])
        return new_builder

    def validate(self, data: dict[str, Any]) -> S:
        return self._schema.model_validate(data)

    async def build(
        self,
        force_load: bool = False,
        *,
        layer_overrides: Mapping[str, RawConfig] | None = None,
    ) -> S:
        """Merge all layers and return a validated schema.

        Untrusted and empty layers are skipped.
        Pass ``force_load=True`` to bypass caching.
        ``layer_overrides`` previews already-validated layer values without
        mutating the layer or its backing store.
        """
        async with self._lock:
            internal_layers = self._layers.copy()
            overrides = layer_overrides or {}

            layer_dicts: list[_LayerData] = []
            for layer in internal_layers:
                try:
                    data = overrides.get(layer.name)
                    if data is None:
                        data = await layer.load(force=force_load)
                    raw = data.model_dump()
                    if raw:
                        layer.check_lower_layers([
                            (below.name, below.data) for below in layer_dicts
                        ])
                        layer_dicts.append(_LayerData(name=layer.name, data=raw))
                except (UntrustedLayerError, EmptyLayerError):
                    continue

            merged, origins, defined_entries = self._merge_fields(
                self._schema, layer_dicts
            )
            config = self._schema.validate_merged(
                merged, origins=origins, defined_entries=defined_entries
            )
            config.check_layer_keys(
                [(ld.name, ld.data) for ld in layer_dicts],
                harness_files=self._harness_files,
            )
            return config

    def _merge_fields(
        self, schema: type[S], layer_dicts: list[_LayerData]
    ) -> tuple[dict[str, Any], dict[str, str], dict[str, dict[str, tuple[str, ...]]]]:
        accumulated: dict[str, Any] = defaultdict(dict)
        origins: dict[str, str] = {}
        defined_entries: dict[str, dict[str, tuple[str, ...]]] = defaultdict(dict)

        for ld in layer_dicts:
            for key, value in ld.data.items():
                if key not in schema.model_fields:
                    continue

                field_info = schema.model_fields[key]
                annotation = field_info.annotation
                if annotation is None:
                    continue

                is_fragment = isinstance(annotation, type) and issubclass(
                    annotation, ConfigFragment
                )
                if is_fragment:
                    if not isinstance(value, dict):
                        continue

                    merged_fragment = False
                    for fragment_key, fragment_value in value.items():
                        if fragment_key not in annotation.model_fields:
                            continue

                        fragment_field = annotation.model_fields[fragment_key]
                        fragment_meta = MergeFieldMetadata.from_field(fragment_field)
                        if fragment_meta is None:
                            continue

                        fragment_value = self._apply_model_before_validators(
                            fragment_key, fragment_field, fragment_value
                        )
                        self._validate_merge_value(
                            f"{key}.{fragment_key}",
                            ld.name,
                            fragment_meta.merge_strategy,
                            fragment_value,
                        )
                        accumulated[key][fragment_key] = (
                            fragment_meta.merge_strategy.apply(
                                accumulated[key].get(fragment_key),
                                fragment_value,
                                key_fn=self._make_key_fn(fragment_meta),
                            )
                        )
                        merged_fragment = True
                    if merged_fragment:
                        origins[key] = ld.name
                    continue

                meta = MergeFieldMetadata.from_field(field_info)
                if meta is None:
                    continue

                value = self._apply_model_before_validators(key, field_info, value)
                accumulated[key] = self._merge_value(
                    key, meta, accumulated, value, layer_name=ld.name
                )
                origins[key] = ld.name
                if meta.merge_strategy is MergeStrategy.DEEP_MERGE and isinstance(
                    value, dict
                ):
                    defined = tuple(
                        entry
                        for entry, payload in value.items()
                        if schema.defines_entry(key, payload)
                    )
                    if defined:
                        defined_entries[key][ld.name] = defined

        return accumulated, origins, dict(defined_entries)

    def _validate_merge_value(
        self, field_name: str, layer_name: str, strategy: MergeStrategy, value: Any
    ) -> None:
        if value is None:
            return
        if strategy in {MergeStrategy.CONCAT, MergeStrategy.UNION}:
            if isinstance(value, list) or isinstance(value, dict) and not value:
                return
            raise ConfigMergeError(field_name, layer_name, "list", value)
        if strategy in {
            MergeStrategy.MERGE,
            MergeStrategy.DEEP_MERGE,
        } and not isinstance(value, dict):
            raise ConfigMergeError(field_name, layer_name, "dictionary", value)

    def _merge_value(
        self,
        key: str,
        meta: MergeFieldMetadata,
        accumulated: dict[str, Any],
        value: Any,
        *,
        layer_name: str,
    ) -> Any:
        """Combine one field across layers, reporting a shape clash as user error.

        A strategy raises ``TypeError`` when the layers disagree on shape, which
        in practice means a setting was typed as a scalar where a table or list
        belongs. That is the user's config, not a bug, so it is re-raised as a
        ``ValueError`` naming the field and the layer -- the startup path prints
        that and exits, instead of showing a traceback.

        Neither swallowing it nor keeping one side is safe: the schema coerces
        several of these fields rather than rejecting them, so a mistyped
        ``tools`` would validate as ``{}`` and silently drop the permissions a
        lower layer had set.
        """
        try:
            return meta.merge_strategy.apply(
                accumulated.get(key), value, key_fn=self._make_key_fn(meta)
            )
        except TypeError as error:
            if key == "mcp_servers" and isinstance(value, dict):
                raise ConfigMergeError(key, layer_name, "list", value) from error
            raise ValueError(
                f"Invalid configuration for '{key}' in the {layer_name} layer: "
                f"it is a {type(value).__name__}, which cannot be combined with "
                f"the value another layer provides. Fix or remove that setting."
            ) from error

    def _apply_model_before_validators(
        self, field_name: str, field_info: FieldInfo, value: Any
    ) -> Any:
        if field_name != "models":
            return value

        for item in field_info.metadata:
            if isinstance(item, BeforeValidator):
                func = cast(Callable[[Any], Any], item.func)
                value = func(value)
        return value

    def _make_key_fn(
        self, merge_field_meta: MergeFieldMetadata
    ) -> Callable[[Any], str] | None:
        merge_key = merge_field_meta.merge_key
        if merge_key is None:
            return None

        return lambda item: item[merge_key]
