"""The GrowthBook filter over the model catalog.

GrowthBook hides catalog models from a cohort, orders them, picks the default
and adjusts their settings. It reaches only the users GrowthBook reaches, so
hiding a model is not access control.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from pydantic import ValidationError

from vibe.core.config._model_aliases import CATALOG_LAYER, build_alias_index
from vibe.core.config.models import ModelConfig
from vibe.observability.logging import logger

# The key the GrowthBook layer carries the parsed filter under. Not a setting:
# the schema has no field for it, so only the config builder reads it.
CATALOG_FILTER_KEY: Final = "model_catalog_filter"
# The GrowthBook layer's name; imported, its module would import this one back.
_GROWTHBOOK_LAYER: Final = "growthbook"

# Not `max_context_length`: the window is a fact of the model the catalog
# owns, and a wrong one would compact every session of a cohort too early.
SETTING_FIELDS: Final = frozenset({
    "thinking",
    "temperature",
    "top_p",
    "max_output_tokens",
    "auto_compact_threshold",
    "display_name",
    "thinking_levels",
    "supports_images",
    "input_price",
    "output_price",
    "cached_input_price",
})

_ID_LIST_KEYS: Final = ("order", "hide")
_PROBE_IDENTITY: Final = {"name": "probe", "provider": "probe", "alias": "probe"}


# Ids are checked against the catalog on every config rebuild: warn once.
_warned_ids: set[tuple[str, str]] = set()


def _warn(message: str) -> None:
    logger.warning("Model catalog filter: %s", message)


def parse_catalog_filter(value: object) -> dict[str, Any] | None:
    """The filter's usable keys, or None when nothing in it applies.

    Every part that cannot apply is dropped on its own with a warning, so one
    mistake never cancels the rest. Unknown keys are dropped silently: a later
    client may read them.
    """
    if not isinstance(value, Mapping):
        _warn(f"ignored, not a JSON object: {value!r}")
        return None
    parsed: dict[str, Any] = {}
    match value.get("default"):
        case None:
            pass
        case str(default) if default:
            parsed["default"] = default
        case default:
            _warn(f"'default' ignored, not a model id: {default!r}")
    for key in _ID_LIST_KEYS:
        if (ids := _parse_ids(key, value.get(key))) is not None:
            parsed[key] = ids
    if (settings := _parse_settings(value.get("settings"))) is not None:
        parsed["settings"] = settings
    return parsed or None


def _parse_ids(key: str, value: object) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        _warn(f"'{key}' ignored, not a list: {value!r}")
        return None
    ids: list[str] = []
    for item in value:
        if isinstance(item, str) and item:
            ids.append(item)
        else:
            _warn(f"'{key}' item ignored, not a model id: {item!r}")
    return ids


def _parse_settings(value: object) -> dict[str, dict[str, Any]] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        _warn(f"'settings' ignored, not an object: {value!r}")
        return None
    settings: dict[str, dict[str, Any]] = {}
    for model_id, fields in value.items():
        if not isinstance(fields, Mapping):
            _warn(f"settings of '{model_id}' ignored, not an object: {fields!r}")
            continue
        accepted = {
            field: parsed
            for field, raw in fields.items()
            if (parsed := _parse_setting(model_id, field, raw)) is not None
        }
        if accepted:
            settings[model_id] = accepted
    return settings


def _parse_setting(model_id: str, field: str, value: object) -> object | None:
    """The value a model config stores for *field*, or None to drop it."""
    if field not in SETTING_FIELDS:
        _warn(f"setting '{field}' of '{model_id}' ignored, not one it can change")
        return None
    try:
        model = ModelConfig.model_validate({**_PROBE_IDENTITY, field: value})
    except ValidationError:
        _warn(f"setting '{field}' of '{model_id}' ignored, invalid: {value!r}")
        return None
    return model.model_dump(include={field})[field]


def with_catalog_filter(
    layers: list[tuple[str, dict[str, Any]]],
) -> list[tuple[str, dict[str, Any]]]:
    """``layers`` with the GrowthBook layer's filter applied to the catalog's
    data. Read from that layer only: a key of the same name in a user's file
    is no filter.
    """
    spec = next(
        (
            data.get(CATALOG_FILTER_KEY)
            for name, data in layers
            if name == _GROWTHBOOK_LAYER
        ),
        None,
    )
    if not spec:
        return layers
    return [
        (name, _apply_catalog_filter(data, spec) if name == CATALOG_LAYER else data)
        for name, data in layers
    ]


def _with_settings(
    payload: Mapping[str, Any], fields: Mapping[str, Any]
) -> dict[str, Any]:
    updated = {**payload, **fields}
    levels = fields.get("thinking_levels")
    # Like a catalog that sent these levels: its model starts on their default.
    if (
        levels is not None
        and "thinking" not in fields
        and payload.get("thinking") not in levels
    ):
        updated["thinking"] = ModelConfig.model_validate({
            **_PROBE_IDENTITY,
            "thinking_levels": levels,
        }).default_thinking
    return updated


def _apply_catalog_filter(
    catalog: Mapping[str, Any], spec: Mapping[str, Any]
) -> dict[str, Any]:
    """The catalog layer's data as if the catalog itself applied *spec*.

    A model is named by its id or one of its catalog aliases, so a rule
    survives a rename that keeps the old id as an alias. A name that matches
    no catalog model is ignored, with a warning. A hidden model loses its
    aliases too, so a pin on any of its names finds no model.
    """
    models = catalog.get("models")
    if not isinstance(models, Mapping) or not models:
        return dict(catalog)
    aliases = catalog.get("catalog_model_aliases")
    targets = build_alias_index(
        aliases if isinstance(aliases, Mapping) else {}, set(models)
    ).targets

    def resolve(key: str, name: str) -> str | None:
        model_id = name if name in models else targets.get(name)
        if model_id is None and (key, name) not in _warned_ids:
            _warned_ids.add((key, name))
            _warn(f"'{key}' names '{name}', which no catalog model has")
        return model_id

    hidden = {
        model_id for name in spec.get("hide", ()) if (model_id := resolve("hide", name))
    }
    kept: dict[str, Any] = {
        model_id: payload
        for model_id, payload in models.items()
        if model_id not in hidden
    }
    first = [
        model_id
        for model_id in dict.fromkeys(
            resolve("order", name) for name in spec.get("order", ())
        )
        if model_id is not None and model_id in kept
    ]
    # Merging into a dict keyed first on the ordered ids keeps their position.
    kept = {**{model_id: kept[model_id] for model_id in first}, **kept}
    for name, fields in spec.get("settings", {}).items():
        model_id = resolve("settings", name)
        if model_id is not None and model_id in kept:
            kept[model_id] = _with_settings(kept[model_id], fields)

    result: dict[str, Any] = {**catalog, "models": kept}
    if isinstance(aliases, Mapping):
        result["catalog_model_aliases"] = {
            model_id: names for model_id, names in aliases.items() if model_id in kept
        }
    if (default := spec.get("default")) is not None and (
        model_id := resolve("default", default)
    ) in kept:
        result["catalog_default_model"] = model_id
    if result.get("catalog_recommended_model") not in kept:
        # No recommendation left: the first remaining model is the default.
        result.pop("catalog_recommended_model", None)
    return result
