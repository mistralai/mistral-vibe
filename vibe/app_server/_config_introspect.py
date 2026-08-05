from __future__ import annotations

from collections.abc import Mapping, Sequence
import enum
from pathlib import Path
from types import UnionType
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic_core import to_jsonable_python

from vibe.app_server.protocol import (
    ConfigFieldKind,
    ConfigFieldWire,
    ConfigLayerValueWire,
)
from vibe.core.config.layer import ConfigLayer, ConfigLayerError, RawConfig
from vibe.core.config.patch import escape_json_pointer_token

DEFAULT_ORIGIN = "default"

# Internal fields populated at runtime (not by the user) that should never be
# rendered in the settings UI.
HIDDEN_SETTINGS: frozenset[str] = frozenset({
    "managed_shell_tools_enabled",
    "routed_default_model",
    "routed_model_config",
    "tools",
})

POPULAR_SETTINGS: frozenset[str] = frozenset({
    "active_model",
    "theme",
    "default_agent",
    "mcp_servers",
    "auto_compact_threshold",
    "bypass_tool_permissions",
    "autocopy_to_clipboard",
    "ask_confirmation_on_exit",
    "enable_notifications",
    "enable_auto_update",
    "voice_mode_enabled",
    "enable_telemetry",
})

_SCALAR_KINDS: tuple[tuple[type, ConfigFieldKind], ...] = (
    (bool, ConfigFieldKind.BOOL),
    (int, ConfigFieldKind.INT),
    (float, ConfigFieldKind.FLOAT),
    (str, ConfigFieldKind.STR),
)


def classify_annotation(annotation: Any) -> tuple[ConfigFieldKind, tuple[str, ...]]:
    """Map a field annotation to its editor kind and any enum choices."""
    if get_origin(annotation) in {Union, UnionType}:
        non_none = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(non_none) == 1:
            annotation = non_none[0]
    if get_origin(annotation) is list:
        args = get_args(annotation)
        item = args[0] if args else None
        if isinstance(item, type) and issubclass(
            item, (str, int, float, bool, Path, enum.Enum)
        ):
            return ConfigFieldKind.LIST, ()
        return ConfigFieldKind.COMPLEX, ()
    if not isinstance(annotation, type):
        return ConfigFieldKind.COMPLEX, ()
    if issubclass(annotation, enum.Enum):
        return ConfigFieldKind.ENUM, tuple(str(member.value) for member in annotation)
    return next(
        (
            (kind, ())
            for scalar_type, kind in _SCALAR_KINDS
            if issubclass(annotation, scalar_type)
        ),
        (ConfigFieldKind.COMPLEX, ()),
    )


async def collect_layer_values(
    layers: Sequence[ConfigLayer[RawConfig]],
) -> dict[str, list[ConfigLayerValueWire]]:
    """Collect each field's per-layer values, highest priority first."""
    values: dict[str, list[ConfigLayerValueWire]] = {}
    for layer in reversed(layers):
        try:
            data = (await layer.load()).model_dump(mode="json")
        except ConfigLayerError:
            continue
        for name, value in data.items():
            values.setdefault(name, []).append(
                ConfigLayerValueWire(layer=layer.name, value=value)
            )
    return values


def build_field_wires(
    config: BaseModel,
    layer_values: Mapping[str, list[ConfigLayerValueWire]],
    *,
    path_prefix: str = "",
    popular: frozenset[str] = frozenset(),
) -> list[ConfigFieldWire]:
    """Build the wire description of every config field for the settings UI."""
    json_values = config.model_dump(mode="json")
    wires: list[ConfigFieldWire] = []
    for name, info in type(config).model_fields.items():
        if name in HIDDEN_SETTINGS:
            continue
        kind, choices = classify_annotation(info.annotation)
        values = list(layer_values.get(name, []))
        if not info.is_required() and not any(
            entry.layer == DEFAULT_ORIGIN for entry in values
        ):
            values.append(
                ConfigLayerValueWire(
                    layer=DEFAULT_ORIGIN,
                    value=to_jsonable_python(
                        info.get_default(call_default_factory=True)
                    ),
                )
            )
        wires.append(
            ConfigFieldWire(
                name=name,
                kind=kind,
                description=(info.description or "").strip(),
                value=json_values.get(name),
                path=f"{path_prefix}/{escape_json_pointer_token(name)}",
                popular=name in popular,
                enum_choices=list(choices),
                layer_values=values,
            )
        )
    return wires
