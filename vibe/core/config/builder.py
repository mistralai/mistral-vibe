from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable
import copy
from dataclasses import dataclass
from typing import Any, cast

from pydantic import BeforeValidator
from pydantic.fields import FieldInfo

from vibe.core.config.layer import (
    ConfigLayer,
    EmptyLayerError,
    RawConfig,
    UntrustedLayerError,
)
from vibe.core.config.schema import ConfigFragment, ConfigSchema, MergeFieldMetadata


@dataclass(frozen=True, slots=True)
class _LayerData:
    name: str
    data: dict[str, Any]


class ConfigBuilder[S: ConfigSchema]:
    """Collects layers and merges them into an immutable Config[S]."""

    def __init__(self, schema: type[S]) -> None:
        self._schema = schema
        self._layers: list[ConfigLayer[RawConfig]] = []
        self._lock = asyncio.Lock()

    def add_layer(self, layer: ConfigLayer[RawConfig]) -> None:
        self._layers.append(layer)

    def add_layers(self, layers: list[ConfigLayer[RawConfig]]) -> None:
        self._layers.extend(layers)

    @property
    def layers(self) -> list[ConfigLayer[RawConfig]]:
        return self._layers

    def copy(self) -> ConfigBuilder[S]:
        """Return a new builder for the same schema with deep-copied layers."""
        new_builder = ConfigBuilder(self._schema)
        new_builder.add_layers([copy.deepcopy(layer) for layer in self._layers])
        return new_builder

    async def build(self, force_load: bool = False) -> S:
        """Merge all layers and return a validated schema.

        Untrusted and empty layers are skipped.
        Pass ``force_load=True`` to bypass caching.
        """
        async with self._lock:
            internal_layers = self._layers.copy()

            layer_dicts: list[_LayerData] = []
            for layer in internal_layers:
                try:
                    data = await layer.load(force=force_load)
                    raw = data.model_dump()
                    if raw:
                        layer_dicts.append(_LayerData(name=layer.name, data=raw))
                except (UntrustedLayerError, EmptyLayerError):
                    continue

            merged, origins = self._merge_fields(self._schema, layer_dicts)
            return self._schema(origins=origins, **merged)

    def _merge_fields(
        self, schema: type[S], layer_dicts: list[_LayerData]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        accumulated: dict[str, Any] = defaultdict(dict)
        origins: dict[str, Any] = {}

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
                        accumulated[key][fragment_key] = (
                            fragment_meta.merge_strategy.apply(
                                accumulated[key].get(fragment_key),
                                fragment_value,
                                key_fn=self._make_key_fn(fragment_meta),
                            )
                        )
                    continue

                meta = MergeFieldMetadata.from_field(field_info)
                if meta is None:
                    continue

                value = self._apply_model_before_validators(key, field_info, value)
                accumulated[key] = meta.merge_strategy.apply(
                    accumulated.get(key), value, key_fn=self._make_key_fn(meta)
                )

        return accumulated, origins

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
