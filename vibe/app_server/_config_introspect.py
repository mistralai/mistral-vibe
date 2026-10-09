from __future__ import annotations

from collections.abc import Mapping, Sequence
import enum
from pathlib import Path
from types import UnionType
from typing import Any, Union, get_args, get_origin
from urllib.parse import urlsplit, urlunsplit

from pydantic_core import to_jsonable_python

from vibe.app_server.protocol import (
    ConfigEffectiveReadResponse,
    ConfigFieldKind,
    ConfigFieldWire,
    ConfigLayerValueWire,
)
from vibe.core.config._defaults import (
    AUTO_COMPACT_WINDOW_RATIO,
    UNSET_AUTO_COMPACT_THRESHOLD,
)
from vibe.core.config.layer import ConfigLayer, ConfigLayerError, RawConfig
from vibe.core.config.layers.admin import AdminConfigLayer
from vibe.core.config.patch import escape_json_pointer_token
from vibe.core.config.vibe_schema import VibeConfigSchema

DEFAULT_ORIGIN = "default"
AUTO_COMPACT_THRESHOLD = "auto_compact_threshold"
MAX_CONTEXT_LENGTH = "max_context_length"
_REDACTED = "<redacted>"
# Config entries whose values are credentials as often as not: header and
# environment mappings, and the command line of a local MCP server.
_SECRET_KEYS = frozenset({"headers", "extra_headers", "env", "args"})
# Only an MCP server's command is redacted: tool configs may have their own.
_MCP_SERVERS_KEY = "mcp_servers"
_MCP_COMMAND_KEY = "command"

# Internal fields populated at runtime (not by the user) that should never be
# rendered in the settings UI.
HIDDEN_SETTINGS: frozenset[str] = frozenset({
    "managed_shell_tools_enabled",
    "routed_default_model",
    "routed_model_config",
    "smart_approve_available",
    "smart_approve_default",
    "routed_extra_models",
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
            # The default layer materializes UNSET for thresholds; it means
            # "no layer set one" and must not surface in the settings UI.
            if name == AUTO_COMPACT_THRESHOLD and value == UNSET_AUTO_COMPACT_THRESHOLD:
                continue
            values.setdefault(name, []).append(
                ConfigLayerValueWire(layer=layer.name, value=value)
            )
        models = data.get("models")
        if not isinstance(models, Mapping):
            continue
        for alias, model in models.items():
            if not isinstance(alias, str) or not isinstance(model, Mapping):
                continue
            for field in (AUTO_COMPACT_THRESHOLD, MAX_CONTEXT_LENGTH):
                if field not in model or (
                    field == AUTO_COMPACT_THRESHOLD
                    and model[field] == UNSET_AUTO_COMPACT_THRESHOLD
                ):
                    continue
                path = _model_field_path(alias, field)
                values.setdefault(path, []).append(
                    ConfigLayerValueWire(layer=layer.name, value=model[field])
                )
    return values


def _model_scoped_wire(
    config: VibeConfigSchema,
    layer_values: Mapping[str, list[ConfigLayerValueWire]],
    name: str,
    *,
    description: str,
    path_prefix: str,
    popular: frozenset[str],
) -> ConfigFieldWire:
    """Build the settings row for an int field that lives on the active model."""
    active_model = config.get_active_model()
    path = _model_field_path(active_model.alias, name, prefix=path_prefix)
    values = list(layer_values.get(path, []))
    if not values:
        values.append(
            ConfigLayerValueWire(
                layer=DEFAULT_ORIGIN, value=getattr(active_model, name)
            )
        )
    return ConfigFieldWire(
        name=name,
        kind=ConfigFieldKind.INT,
        description=description,
        value=getattr(active_model, name),
        path=path,
        popular=name in popular,
        enum_choices=[],
        layer_values=values,
    )


def build_field_wires(
    config: VibeConfigSchema,
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
        value = json_values.get(name)
        path = f"{path_prefix}/{escape_json_pointer_token(name)}"
        values = list(layer_values.get(name, []))
        description = (info.description or "").strip()
        if name == AUTO_COMPACT_THRESHOLD:
            active_model = config.get_active_model()
            value = active_model.auto_compact_threshold
            description = (
                "Token count before automatic compaction for the active model "
                f"({active_model.alias}). Set to 0 to disable automatic compaction."
            )
            model_values = layer_values.get(
                _model_field_path(active_model.alias, name, prefix=path_prefix), []
            )
            if values and values[0].layer == AdminConfigLayer.NAME:
                # A lower-priority, fully materialized model default must not
                # hide the admin provenance that makes the global fallback
                # read-only.
                value = values[0].value
            elif model_values:
                # A layer set the model's own threshold: the row edits that.
                wires.append(
                    _model_scoped_wire(
                        config,
                        layer_values,
                        name,
                        description=description,
                        path_prefix=path_prefix,
                        popular=popular,
                    )
                )
                continue
            elif not values:
                values.append(ConfigLayerValueWire(layer=DEFAULT_ORIGIN, value=value))
        elif not info.is_required() and not any(
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
                description=description,
                value=value,
                path=path,
                popular=name in popular,
                enum_choices=list(choices),
                layer_values=values,
            )
        )
    # ``max_context_length`` lives only on models, so it has no top-level
    # field to iterate; surface the active model's window as its own row.
    wires.append(
        _model_scoped_wire(
            config,
            layer_values,
            MAX_CONTEXT_LENGTH,
            description=(
                "Context window of the active model "
                f"({config.get_active_model().alias}). The compaction threshold "
                f"defaults to {int(AUTO_COMPACT_WINDOW_RATIO * 100)}% of it."
            ),
            path_prefix=path_prefix,
            popular=popular,
        )
    )
    return wires


def _model_field_path(alias: str, field: str, *, prefix: str = "") -> str:
    return (
        f"{prefix}/models/{escape_json_pointer_token(alias)}/"
        f"{escape_json_pointer_token(field)}"
    )


def effective_config_response(
    config: VibeConfigSchema, *, bypass_tool_permissions: bool
) -> ConfigEffectiveReadResponse:
    dumped = config.model_dump(mode="json")
    # Auto-approve is a session option rather than a config layer, so the
    # session's own decision replaces the configured value.
    dumped["bypass_tool_permissions"] = bypass_tool_permissions
    return ConfigEffectiveReadResponse(config=redact_config(dumped))


def redact_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Hide the credentials a dumped config may hold, keeping its shape.

    Header, environment and MCP argument values are replaced whole, and an MCP
    server's ``command`` keeps only its program. Any URL, such as a provider's
    ``api_base`` or an MCP server's ``url``, keeps its host and path but loses
    its user info and query string.
    """
    return _redact(config)


def _redact(value: Any, *, secret: bool = False) -> Any:
    if isinstance(value, Mapping):
        if secret:
            return dict.fromkeys(value, _REDACTED)
        return {
            key: _redact_mcp_servers(item)
            if key == _MCP_SERVERS_KEY
            else _redact(item, secret=key in _SECRET_KEYS)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, secret=secret) for item in value]
    if secret and value is not None:
        return _REDACTED
    if isinstance(value, str):
        return _redact_url(value)
    return value


def _redact_mcp_servers(servers: Any) -> Any:
    redacted = _redact(servers)
    if not isinstance(redacted, list):
        return redacted
    for server in redacted:
        if isinstance(server, dict) and _MCP_COMMAND_KEY in server:
            server[_MCP_COMMAND_KEY] = _redact_command(server[_MCP_COMMAND_KEY])
    return redacted


def _redact_command(command: Any) -> Any:
    # The program says which server runs; what follows it may be a credential.
    match command:
        case str():
            words = command.split(maxsplit=1)
            return f"{words[0]} {_REDACTED}" if len(words) > 1 else command
        case [program, *rest]:
            return [program, *(_REDACTED for _ in rest)]
        case _:
            return command


def _redact_url(value: str) -> str:
    if "://" not in value:
        return value
    try:
        parts = urlsplit(value)
    except ValueError:
        return _REDACTED
    if not parts.scheme or not parts.netloc:
        return value
    netloc = parts.netloc
    if "@" in netloc:
        netloc = f"{_REDACTED}@{netloc.rpartition('@')[2]}"
    query = _REDACTED if parts.query else ""
    return urlunsplit(parts._replace(netloc=netloc, query=query))
