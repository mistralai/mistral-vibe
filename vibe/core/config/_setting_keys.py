"""Settings a user wrote that Vibe does not use: unknown, retired, or without effect.

The merge reads only the keys the schema declares, so a misspelt or retired
setting would otherwise vanish without a word. Each user-written layer is
checked on its own, so the notice names the file the key is in. By default the
config still loads and the notice is a validation warning; with
``strict_config`` on, the config is refused instead.

Only layers a person writes are checked. The admin, GrowthBook and model
catalog layers come from a server that may be newer than this Vibe, and the
environment layer only ever carries declared fields.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
import difflib
from enum import StrEnum
from functools import cache
import os
import types
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import AliasChoices, AliasPath, BaseModel, TypeAdapter, ValidationError
from pydantic.fields import FieldInfo
from pydantic_core import to_jsonable_python

from vibe.core.config.models import _LEGACY_STATIC_AUTH_KEYS

# Layer name -> how a notice names it.
CHECKED_LAYERS: Mapping[str, str] = {
    "user-toml": "your config.toml",
    "user-supplied": "the supplied user config",
    "project-toml": "the project's .vibe/config.toml",
    "agent-profile": "the active agent profile",
}

# Builtin tools the unified harness never runs, so none of their settings is
# read, with what replaces each.
TOOLS_WITHOUT_EFFECT: Mapping[str, str] = {
    "grep": (
        "there is no grep tool, and the agent searches through the shell, so `rg`"
        " and `grep` follow your shell tool's settings, such as [tools.bash]"
    ),
    "task": "subagents are started with the built-in `agents.spawn` tool",
}

# Keys a tool still accepts that nothing reads with the unified harness, which
# runs these tools itself.
TOOL_SETTINGS_WITHOUT_EFFECT: Mapping[str, frozenset[str]] = {
    "read_file": frozenset({"max_read_bytes"}),
    "write_file": frozenset({"max_write_bytes", "create_parent_dirs"}),
    "web_fetch": frozenset({
        "default_timeout",
        "max_timeout",
        "max_content_bytes",
        "user_agent",
    }),
    "web_search": frozenset({"model", "timeout"}),
    **{
        f"{shell}{suffix}": frozenset({"max_inline_bytes"})
        for shell in ("bash", "git_bash", "powershell")
        for suffix in ("", "_sessions", "_log_file")
    },
    **{
        f"{shell}_output": frozenset({"max_inline_bytes", "max_poll_seconds"})
        for shell in ("bash", "git_bash", "powershell")
    },
}

# Settings that Vibe up to July 2026 wrote into a new config.toml with their
# defaults, and that it has dropped since, with the value it wrote. A file
# that still holds that value asked for nothing, so it is not reported.
_FORMER_DEFAULTS: Mapping[str, Any] = {
    "auto_approve": False,
    "experiment_overrides": {},
    "experimental_bash_tool": False,
    "vibe_code_api_key_env_var": "MISTRAL_API_KEY",
    "vibe_code_enabled": True,
    "vim_keybindings": False,
}
# The same for tool settings, including those whose default has changed.
_FORMER_TOOL_DEFAULTS: Mapping[str, Mapping[str, Any]] = {
    "edit": {"fuzzy_threshold": 0.9},
    "grep": {"sensitive_patterns": ["**/.env", "**/.env.*"]},
    "read_file": {"max_read_bytes": 51200},
    "web_fetch": {"max_content_bytes": 512000},
}

# Keys a model's ``mode="before"`` validator reads and rewrites, so they are
# not fields but are still used.
_LEGACY_INPUT_KEYS: Mapping[str, frozenset[str]] = {
    "MCPHttp": frozenset(_LEGACY_STATIC_AUTH_KEYS),
    "MCPStreamableHttp": frozenset(_LEGACY_STATIC_AUTH_KEYS),
}


# Nesting the check follows, which covers every model-typed setting today.
_MAX_DEPTH = 4


class KeyIssueKind(StrEnum):
    UNKNOWN = "unknown"
    RETIRED = "retired"
    NO_EFFECT = "no_effect"


@dataclass(frozen=True, slots=True)
class KeyIssue:
    layer: str
    path: str
    kind: KeyIssueKind
    suggestion: str | None = None
    reason: str | None = None

    def describe(self) -> str:
        match self.kind:
            case KeyIssueKind.UNKNOWN:
                hint = (
                    f" (did you mean `{self.suggestion}`?)" if self.suggestion else ""
                )
                return f"`{self.path}` is not a Vibe setting{hint}"
            case KeyIssueKind.RETIRED:
                return f"`{self.path}` is no longer used"
            case KeyIssueKind.NO_EFFECT:
                why = f" ({self.reason})" if self.reason else ""
                return f"`{self.path}` has no effect{why}"


class UnusedConfigKeysError(ValueError):
    """The config carries settings Vibe does not use, and ``strict_config`` is on."""

    def __init__(self, issues: Sequence[KeyIssue]) -> None:
        self.issues = tuple(issues)
        lines = [
            f"- {issue.describe()}, in {CHECKED_LAYERS.get(issue.layer, issue.layer)}"
            for issue in self.issues
        ]
        super().__init__(
            "Config refused because strict_config is on and it has settings "
            "Vibe does not use:\n" + "\n".join(lines)
        )


STRICT_CONFIG_ENV = "VIBE_STRICT_CONFIG"


class InvalidStrictConfigError(UnusedConfigKeysError):
    """``VIBE_STRICT_CONFIG`` is set to a value that is not a boolean.

    The environment layer drops a value it cannot parse, which here would turn
    strict mode off without a word, so the config is refused instead.
    """

    def __init__(self, name: str, value: str) -> None:
        self.issues = ()
        ValueError.__init__(
            self,
            f"Config refused because {name}={value!r} is not a boolean. Use"
            " 1, true, yes or on to refuse unused settings, or 0, false, no or"
            " off to warn about them.",
        )


def check_strict_config_env(environ: Mapping[str, str] | None = None) -> None:
    """Refuse a ``VIBE_STRICT_CONFIG`` value the environment layer would drop."""
    environ = os.environ if environ is None else environ
    for name, value in environ.items():
        # The environment layer reads VIBE_* names case-insensitively and skips empty values.
        if name.upper() != STRICT_CONFIG_ENV or not value:
            continue
        try:
            TypeAdapter(bool).validate_python(value)
        except ValidationError:
            raise InvalidStrictConfigError(name, value) from None


def find_unused_keys(
    schema: type[BaseModel],
    layers: Iterable[tuple[str, Mapping[str, Any]]],
    *,
    custom_tools: Collection[str] = (),
) -> list[KeyIssue]:
    """Every key in a checked layer that the schema does not use, in layer order.

    ``custom_tools`` names the custom tools that may replace a builtin of the
    same name; their settings are theirs to read, so they are not checked.
    """
    issues: list[KeyIssue] = []
    for layer, data in layers:
        if layer not in CHECKED_LAYERS:
            continue
        for key, value in data.items():
            field = schema.model_fields.get(key)
            if field is None:
                if key in _FORMER_DEFAULTS:
                    if not _same(value, _FORMER_DEFAULTS[key]):
                        issues.append(KeyIssue(layer, key, KeyIssueKind.RETIRED))
                    continue
                issues.append(_unknown(layer, key, key, _field_keys(schema)))
            elif field.deprecated:
                if not _is_default(value, [field]):
                    issues.append(KeyIssue(layer, key, KeyIssueKind.RETIRED))
            elif key == "tools":
                issues.extend(_tool_issues(layer, value, custom_tools))
            else:
                issues.extend(_nested_issues(layer, key, field.annotation, value))
    return issues


# Keys one notice names before it only counts the rest.
NOTICE_KEY_LIMIT = 6


def notice(issues: Sequence[KeyIssue]) -> list[str]:
    """One warning per layer, naming its keys up to ``NOTICE_KEY_LIMIT``."""
    by_layer: dict[str, list[KeyIssue]] = {}
    for issue in issues:
        by_layer.setdefault(issue.layer, []).append(issue)
    messages: list[str] = []
    for layer, layer_issues in by_layer.items():
        named = "; ".join(i.describe() for i in layer_issues[:NOTICE_KEY_LIMIT])
        rest = len(layer_issues) - NOTICE_KEY_LIMIT
        more = f"; and {rest} more" if rest > 0 else ""
        messages.append(
            f"Settings in {CHECKED_LAYERS.get(layer, layer)} that Vibe ignores: "
            f"{named}{more}. Remove them, or fix the spelling."
        )
    return messages


def _unknown(layer: str, path: str, key: str, known: Iterable[str]) -> KeyIssue:
    close = difflib.get_close_matches(key, list(known), n=1, cutoff=0.75)
    return KeyIssue(layer, path, KeyIssueKind.UNKNOWN, close[0] if close else None)


def _field_keys(model: type[BaseModel]) -> set[str]:
    keys: set[str] = set()
    for name, field in model.model_fields.items():
        keys.update(_input_names(name, field))
    return keys | _LEGACY_INPUT_KEYS.get(model.__name__, frozenset())


def _input_names(name: str, field: FieldInfo) -> list[str]:
    """Every key a field reads: its name, its alias and each validation alias."""
    names = [name, *([field.alias] if field.alias else [])]
    aliases = field.validation_alias
    choices = aliases.choices if isinstance(aliases, AliasChoices) else [aliases]
    for choice in choices:
        if isinstance(choice, AliasPath):
            choice = choice.path[0]
        if isinstance(choice, str) and choice not in names:
            names.append(choice)
    return names


def _checks_extras(model: type[BaseModel]) -> bool:
    # ``allow`` keeps extras for the model's own use and ``forbid`` already
    # fails validation, so only an ignoring model drops a key unseen.
    return model.model_config.get("extra") in {None, "ignore"}


def _models_in(annotation: Any) -> tuple[type[BaseModel], ...]:
    """The models one value of ``annotation`` may hold, through Optional and unions."""
    origin = get_origin(annotation)
    if origin is Annotated:
        return _models_in(get_args(annotation)[0])
    if origin in {Union, types.UnionType}:
        return tuple(m for arg in get_args(annotation) for m in _models_in(arg))
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return (annotation,)
    return ()


def _entries(annotation: Any, value: Any) -> tuple[Any, Iterable[tuple[str, Any]]]:
    """Split a container field into its item type and its (label, item) pairs.

    A ``dict[str, Model]`` field such as ``models`` is also written as a TOML
    array of tables, so either shape is read.
    """
    origin = get_origin(annotation)
    if origin is Annotated:
        return _entries(get_args(annotation)[0], value)
    if origin in {list, tuple, set, frozenset} and get_args(annotation):
        item = get_args(annotation)[0]
        if isinstance(value, list):
            return item, ((f"[{i}]", entry) for i, entry in enumerate(value))
        return item, ()
    if origin is dict and get_args(annotation):
        item = get_args(annotation)[-1]
        if isinstance(value, Mapping):
            return item, ((f".{k}", entry) for k, entry in value.items())
        if isinstance(value, list):
            return item, ((f"[{i}]", entry) for i, entry in enumerate(value))
    return None, ()


def _nested_issues(
    layer: str, path: str, annotation: Any, value: Any, depth: int = 0
) -> list[KeyIssue]:
    if depth > _MAX_DEPTH:
        return []
    if models := _models_in(annotation):
        return _model_issues(layer, path, models, value, depth)
    item, entries = _entries(annotation, value)
    if item is None or not _models_in(item):
        return []
    issues: list[KeyIssue] = []
    for label, entry in entries:
        issues.extend(_nested_issues(layer, f"{path}{label}", item, entry, depth + 1))
    return issues


def _model_issues(
    layer: str, path: str, models: tuple[type[BaseModel], ...], value: Any, depth: int
) -> list[KeyIssue]:
    if not isinstance(value, Mapping):
        return []
    candidates = _discriminated(models, value)
    if not all(_checks_extras(model) for model in candidates):
        return []
    known: set[str] = set().union(*(_field_keys(model) for model in candidates))
    issues: list[KeyIssue] = []
    for key, entry in value.items():
        if key not in known:
            issues.append(_unknown(layer, f"{path}.{key}", key, known))
            continue
        fields = [
            model.model_fields[key] for model in candidates if key in model.model_fields
        ]
        if len(fields) == 1:
            issues.extend(
                _nested_issues(
                    layer, f"{path}.{key}", fields[0].annotation, entry, depth + 1
                )
            )
    return issues


def _discriminated(
    models: tuple[type[BaseModel], ...], value: Mapping[str, Any]
) -> tuple[type[BaseModel], ...]:
    """Narrow a union to the member whose literal tag the value carries."""
    if len(models) == 1:
        return models
    matching = tuple(
        model
        for model in models
        if all(
            _literal_accepts(field, value[name])
            for name, field in model.model_fields.items()
            if name in value and _literal_values(field) is not None
        )
    )
    return matching or models


def _literal_values(field: FieldInfo) -> tuple[Any, ...] | None:
    if get_origin(field.annotation) is Literal:
        return get_args(field.annotation)
    return None


def _literal_accepts(field: FieldInfo, candidate: Any) -> bool:
    values = _literal_values(field)
    return values is None or candidate in values


def _tool_issues(
    layer: str, tools: Any, custom_tools: Collection[str]
) -> list[KeyIssue]:
    if not isinstance(tools, Mapping):
        return []
    catalog = builtin_tool_settings()
    issues: list[KeyIssue] = []
    for name, settings in tools.items():
        known = catalog.get(name)
        # A custom or MCP tool keeps whatever its own config reads, also when it
        # takes a builtin's name.
        if known is None or name in custom_tools or not isinstance(settings, Mapping):
            continue
        variants = builtin_tool_config_classes()[name]
        no_effect = _no_effect_keys(name, known, variants)
        former = _FORMER_TOOL_DEFAULTS.get(name, {})
        for key, value in settings.items():
            path = f"tools.{name}.{key}"
            if key not in known:
                if key in former:
                    if not _same(value, former[key]):
                        issues.append(KeyIssue(layer, path, KeyIssueKind.RETIRED))
                    continue
                issues.append(_unknown(layer, path, key, known))
            elif key in no_effect:
                fields = [
                    field
                    for c in variants
                    for field_name, field in c.model_fields.items()
                    if key in _input_names(field_name, field)
                ]
                was_default = key in former and _same(value, former[key])
                if not was_default and not _is_default(value, fields):
                    issues.append(
                        KeyIssue(
                            layer,
                            path,
                            KeyIssueKind.NO_EFFECT,
                            reason=TOOLS_WITHOUT_EFFECT.get(name),
                        )
                    )
    return issues


def _settings_without_effect(name: str, known: frozenset[str]) -> frozenset[str]:
    if name in TOOLS_WITHOUT_EFFECT:
        return known
    return TOOL_SETTINGS_WITHOUT_EFFECT.get(name, frozenset())


def _no_effect_keys(
    name: str, known: frozenset[str], variants: Iterable[type[BaseModel]]
) -> frozenset[str]:
    """The keys without effect, with every other name their fields read."""
    fields = _settings_without_effect(name, known)
    return fields | {
        key
        for config_class in variants
        for field_name, field in config_class.model_fields.items()
        if field_name in fields
        for key in _input_names(field_name, field)
    }


def _same(value: Any, default: Any) -> bool:
    return to_jsonable_python(value) == to_jsonable_python(default)


def _is_default(value: Any, fields: Sequence[FieldInfo]) -> bool:
    """Whether ``value`` is a default of one of ``fields``.

    An unused setting left at its default asks for nothing, so dropping it
    changes nothing. Old Vibe versions wrote every default into config.toml.
    """
    return any(
        _same(value, field.get_default(call_default_factory=True))
        for field in fields
        if not field.is_required()
    )


@cache
def builtin_tool_config_classes() -> Mapping[str, tuple[type[BaseModel], ...]]:
    """Each builtin tool name with the config classes of every variant of it.

    A name can stand for several tools, picked by platform or rollout, so its
    settings are those of all of them.
    """
    from vibe.core.paths import DEFAULT_TOOL_DIR
    from vibe.core.tools.manager import ToolManager

    classes: dict[str, list[type[BaseModel]]] = {}
    for tool_class in ToolManager._iter_tool_classes([DEFAULT_TOOL_DIR.path]):
        config_class = tool_class._get_tool_config_class()
        variants = classes.setdefault(tool_class.get_name(), [])
        if config_class not in variants:
            variants.append(config_class)
    return {name: tuple(variants) for name, variants in classes.items()}


@cache
def builtin_tool_settings() -> Mapping[str, frozenset[str]]:
    return {
        name: frozenset(set().union(*(_field_keys(c) for c in variants)))
        for name, variants in builtin_tool_config_classes().items()
    }


def tool_settings_json_schema() -> dict[str, Any]:
    """A JSON schema for each builtin tool's ``[tools.<name>]`` table.

    Settings without effect are marked ``deprecated``. A name not listed here
    is a custom or MCP tool, which takes any keys.
    """
    schemas: dict[str, Any] = {}
    for name, variants in sorted(builtin_tool_config_classes().items()):
        properties: dict[str, Any] = {}
        for config_class in variants:
            schema = config_class.model_json_schema(mode="validation")
            defs = schema.get("$defs", {})
            for key, prop in schema.get("properties", {}).items():
                properties.setdefault(key, _inline_refs(prop, defs))
            # The schema names one key per field; list every key it reads, so
            # an editor accepts what validation accepts.
            for field_name, field in config_class.model_fields.items():
                names = _input_names(field_name, field)
                listed = next((n for n in names if n in properties), None)
                if listed is not None:
                    for key in names:
                        properties.setdefault(key, properties[listed])
        for key in _no_effect_keys(name, frozenset(properties), variants):
            if key in properties:
                properties[key] = {
                    **properties[key],
                    "deprecated": True,
                    "description": "No effect with the unified harness.",
                }
        schemas[name] = {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }
    return schemas


def config_json_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """``schema``'s JSON schema, with each builtin tool's settings under ``tools``."""
    document = schema.model_json_schema(mode="serialization", by_alias=True)
    # Vibe warns about any other top-level key, so an editor can flag it too.
    document["additionalProperties"] = False
    tools = document.get("properties", {}).get("tools")
    if isinstance(tools, dict):
        tools["properties"] = tool_settings_json_schema()
        tools["description"] = (
            "Settings per tool. A builtin tool takes the keys listed here; a"
            " custom or MCP tool takes any keys."
        )
    return document


def _inline_refs(node: Any, defs: Mapping[str, Any]) -> Any:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            target = defs.get(ref.removeprefix("#/$defs/"), {})
            rest = {k: v for k, v in node.items() if k != "$ref"}
            return {**_inline_refs(target, defs), **_inline_refs(rest, defs)}
        return {key: _inline_refs(value, defs) for key, value in node.items()}
    if isinstance(node, list):
        return [_inline_refs(item, defs) for item in node]
    return node


__all__ = [
    "CHECKED_LAYERS",
    "STRICT_CONFIG_ENV",
    "TOOLS_WITHOUT_EFFECT",
    "TOOL_SETTINGS_WITHOUT_EFFECT",
    "InvalidStrictConfigError",
    "KeyIssue",
    "KeyIssueKind",
    "UnusedConfigKeysError",
    "builtin_tool_config_classes",
    "builtin_tool_settings",
    "check_strict_config_env",
    "config_json_schema",
    "find_unused_keys",
    "notice",
    "tool_settings_json_schema",
]
